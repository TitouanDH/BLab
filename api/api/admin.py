from django.contrib import admin, messages
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from . import admin_actions, tools
from .quarantine import CleanupInProgress, lift
from .release import AlreadyReleased, release
from .reservations import MAX_LENGTH, SLACK
from .switch_worker import request_cleanup
from .models import (BaselineExtra, PendingCleanup, PermanentCable, Port, Project, ProjectMember, Quarantine,
                     Reservation, Restore, RestoreSwitch, Snapshot, SnapshotSwitch, Switch, SwitchAccount,
                     SwitchEvent, SwitchPassword, Tool, TopologyShare)


class PermanentCableInline(admin.TabularInline):
    """Ports an admin marks as permanently cabled: an Inspection doesn't count them as Unwanted cables."""
    model = PermanentCable
    extra = 0


def _mark(model_admin, request, queryset, mark, done_as):
    """Runs mark (api.tools) on each record, and says what became of each."""
    for record in queryset:
        try:
            extra = mark(record, request.user) or []
        except tools.ToolError as e:
            model_admin.message_user(request, str(e), messages.ERROR)
            continue
        model_admin.log_change(request, record, f"Marked as a {done_as}")
        model_admin.message_user(request, '; '.join([f"{record} is now a {done_as}", *extra]) + '.')


def _each(model_admin, request, records, act):
    """Runs act(record) on each record, and says what it did, or why it was refused or failed."""
    for record in records:
        try:
            ok, outcome = act(record)
        except (admin_actions.Refused, CleanupInProgress, AlreadyReleased) as e:
            model_admin.message_user(request, str(e), messages.WARNING)
            continue
        model_admin.message_user(request, outcome, messages.INFO if ok else messages.ERROR)


def _release(reservation, admin, force):
    """An admin's Release (api.release): whether it ended the Reservation, and what it did."""
    ip = reservation.switch.mngt_IP
    result = release(reservation, admin, by_admin=True, force=force)
    # A forced Release may have forgotten Links and still been refused, by a Link made meanwhile
    forgot = (f"; forgot {len(result.forgotten)} Link(s), left as Orphans: " + '; '.join(result.forgotten)
              if result.forgotten else '')
    if not result.released:
        hint = '' if force else " (Force release forgets the Links that can't be torn down)"
        return False, f"{ip} stays reserved{hint}: " + '; '.join(result.failures) + forgot
    return True, f"Released {ip} (held by {reservation.user.username}); it will be Cleaned up" + forgot


class AdminReleaseActions:
    """Release and Force release (api.release, by an admin); reservations(request, queryset) gives the Reservations."""

    @admin.action(description='Release (tears its Links down and Cleans it up; a Quarantine names nobody)')
    def admin_release(self, request, queryset):
        _each(self, request, self.reservations(request, queryset), lambda r: _release(r, request.user, False))

    @admin.action(description="Force release (also forgets the Links that can't be torn down)")
    def force_release(self, request, queryset):
        _each(self, request, self.reservations(request, queryset), lambda r: _release(r, request.user, True))


class SwitchCareActions:
    """Inspect now and Clean up now (api.admin_actions); switches(queryset) gives the Switches."""

    def switches(self, queryset):
        return queryset

    @admin.action(description='Inspect now (on a Switch not reserved, lifts or opens a Quarantine)')
    def inspect_now(self, request, queryset):
        _each(self, request, self.switches(queryset), lambda s: (True, admin_actions.inspect_now(s, request.user)))

    @admin.action(description='Clean up now (not on a reserved Switch; a Quarantine names nobody)')
    def clean_up_now(self, request, queryset):
        _each(self, request, self.switches(queryset), lambda s: (True, admin_actions.clean_up_now(s, request.user)))


@admin.register(Switch)
class SwitchAdmin(AdminReleaseActions, SwitchCareActions, admin.ModelAdmin):
    """
    Out of service is set here: a reason takes the Switch out of reservation, emptying it puts it back.
    A record that isn't a Switch, such as the IXIA, is marked as a Tool: it moves to Tools with its Ports.
    Release, Inspect now and Clean up now act on the selected Switches.
    """
    inlines = [PermanentCableInline]
    list_display = ('mngt_IP', 'model', 'reserved_by', 'out_of_service_reason')
    search_fields = ('mngt_IP', 'model')
    readonly_fields = ('out_of_service_since',)
    exclude = ('is_tool',)
    actions = ['admin_release', 'force_release', 'inspect_now', 'clean_up_now', 'mark_tool']

    def get_queryset(self, request):
        holder = Reservation.objects.filter(switch=OuterRef('pk')).values('user__username')[:1]
        return super().get_queryset(request).annotate(holder_name=Subquery(holder))

    @admin.display(description='Reserved by', ordering='holder_name')
    def reserved_by(self, switch):
        return switch.holder_name or ''

    def reservations(self, request, queryset):
        for switch in queryset:
            reservation = Reservation.objects.filter(switch=switch).select_related('switch', 'user').first()
            if reservation is None:
                self.message_user(request, f"{switch.mngt_IP} is not reserved: nothing to Release", messages.WARNING)
            else:
                yield reservation

    @admin.action(description='Mark as a Tool (such as the IXIA): it moves to Tools with its Ports')
    def mark_tool(self, request, queryset):
        _mark(self, request, queryset, tools.mark_tool, 'Tool')

    def save_model(self, request, obj, form, change):
        obj.out_of_service_reason = (obj.out_of_service_reason or '').strip() or None
        was = Switch.objects.filter(pk=obj.pk).values_list('out_of_service_reason', flat=True).first() if change else None
        if obj.out_of_service_reason != was:
            obj.out_of_service_since = timezone.now() if obj.out_of_service_reason else None
        super().save_model(request, obj, form, change)
        if obj.out_of_service_reason and obj.out_of_service_reason != was:
            SwitchEvent.objects.create(switch=obj, kind=SwitchEvent.OUT_OF_SERVICE, ok=False,
                                       reasons=[obj.out_of_service_reason], user=request.user)
        elif was and not obj.out_of_service_reason:
            SwitchEvent.objects.create(switch=obj, kind=SwitchEvent.BACK_IN_SERVICE, ok=True, user=request.user)
            # A Release while it was Out of service deferred its Cleanup (api.release), and whatever was
            # done to it meanwhile is unknown: the next holder must still get a clean Switch (ADR 0006)
            # (a Cleanup already asked for keeps the holder it names)
            if not (Reservation.objects.filter(switch=obj).exists()
                    or PendingCleanup.objects.filter(switch=obj).exists()):
                request_cleanup(obj, None)


@admin.register(SwitchEvent)
class SwitchEventAdmin(admin.ModelAdmin):
    list_display = ('switch', 'kind', 'at', 'ok')
    list_filter = ('kind', 'ok')


class QuarantineStateFilter(admin.SimpleListFilter):
    """Open Quarantines unless asked otherwise: they are the ones to act on."""
    title = 'state'
    parameter_name = 'state'

    def lookups(self, request, model_admin):
        return [('lifted', 'Lifted'), ('all', 'All')]

    def choices(self, changelist):
        yield {'selected': self.value() is None, 'display': 'Open',
               'query_string': changelist.get_query_string(remove=[self.parameter_name])}
        for value, title in self.lookup_choices:
            yield {'selected': self.value() == value, 'display': title,
                   'query_string': changelist.get_query_string({self.parameter_name: value})}

    def queryset(self, request, queryset):
        if self.value() == 'all':
            return queryset
        return queryset.filter(lifted_at__isnull=self.value() != 'lifted')


@admin.register(Quarantine)
class QuarantineAdmin(SwitchCareActions, admin.ModelAdmin):
    """
    An admin lifts the Quarantines that name nobody, or any other when it has to be, and may
    un-name a holder so they can reserve again; Inspect now (a Re-check) and Clean up now act
    on the Quarantined Switches.
    """
    list_display = ('switch_ip', 'holder', 'reasons_shown', 'opened_at', 'lifted_at')
    list_filter = (QuarantineStateFilter,)
    list_select_related = ('switch', 'holder')
    search_fields = ('switch__mngt_IP', 'holder__username')
    readonly_fields = ('switch', 'holder', 'opened_at', 'reasons', 'lifted_at')
    actions = ['lift', 'unname', 'inspect_now', 'clean_up_now']

    @admin.display(description='Switch', ordering='switch__mngt_IP')
    def switch_ip(self, quarantine):
        return quarantine.switch.mngt_IP

    @admin.display(description='Reasons')
    def reasons_shown(self, quarantine):
        return '; '.join(quarantine.reasons)

    def has_add_permission(self, request):
        return False  # BLab opens Quarantines; an admin puts a Switch Out of service instead

    def switches(self, queryset):
        return Switch.objects.filter(id__in=queryset.values('switch_id')).order_by('mngt_IP')

    @admin.action(description='Un-name the holder (stays open, for an admin; they may reserve again)')
    def unname(self, request, queryset):
        _each(self, request, queryset.select_related('switch', 'holder'),
              lambda q: (True, admin_actions.unname(q, request.user)))

    @admin.action(description='Lift the selected Quarantines')
    def lift(self, request, queryset):
        for quarantine in queryset.filter(lifted_at__isnull=True):
            lift(quarantine, request.user, f'lifted by {request.user.username} (admin)')


@admin.register(Reservation)
class ReservationAdmin(AdminReleaseActions, admin.ModelAdmin):
    """
    Admins may set any end date: one beyond the limits makes the Reservation an admin
    exception (api.reservations), unless the admin sets the box themselves.
    A Reservation ends by a Release, never by deleting it, which would leave its Links up. So
    the admin site also refuses to delete a Switch or a user that holds one: Release it first.
    """
    list_display = ('switch', 'user', 'project', 'end_date', 'renewals', 'admin_exception')
    list_filter = ('admin_exception',)
    list_select_related = ('switch', 'user', 'project')
    search_fields = ('switch__mngt_IP', 'user__username')
    actions = ['admin_release', 'force_release']

    def has_delete_permission(self, request, obj=None):
        return False

    def reservations(self, request, queryset):
        return queryset.select_related('switch', 'user')

    def save_model(self, request, obj, form, change):
        if 'end_date' in form.changed_data and 'admin_exception' not in form.changed_data:
            obj.admin_exception = obj.end_date is None or obj.end_date > timezone.now() + MAX_LENGTH + SLACK
        super().save_model(request, obj, form, change)


@admin.register(PendingCleanup)
class PendingCleanupAdmin(admin.ModelAdmin):
    list_display = ('switch', 'holder', 'requested_at', 'started_at', 'give_up_at')


@admin.register(SwitchAccount)
class SwitchAccountAdmin(admin.ModelAdmin):
    """Read-only: api.switch_accounts keeps them. The password is its user's alone, so it isn't shown."""
    list_display = ('switch', 'name', 'user', 'created', 'error', 'tried_at', 'switch_password_at')
    exclude = ('password',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SwitchPassword)
class SwitchPasswordAdmin(admin.ModelAdmin):
    """Read-only: only its user chooses it (api.switch_password), and nobody sees it, encrypted or not."""
    list_display = ('user', 'changed_at')
    exclude = ('encrypted',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ToolPortInline(admin.TabularInline):
    """A Tool's ports, each paired with its UNI. One is taken while it carries a Link."""
    model = Port
    extra = 0
    fields = ('port_switch', 'backbone', 'port_backbone', 'svlan')
    readonly_fields = ('svlan',)


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    """Tools (api.tools): never reserved, Cleaned up or Inspected. Their Ports are linked like a Switch's."""
    inlines = [ToolPortInline]
    list_display = ('model', 'mngt_IP')
    fields = ('model', 'mngt_IP', 'part_number', 'serial_number')
    actions = ['mark_switch']

    def save_model(self, request, obj, form, change):
        obj.is_tool = True
        # Fields a Tool doesn't use, which the table still requires
        obj.console = obj.console or ''
        obj.hardware_revision = obj.hardware_revision or ''
        super().save_model(request, obj, form, change)

    @admin.action(description='Mark back as a Switch')
    def mark_switch(self, request, queryset):
        _mark(self, request, queryset, tools.mark_switch, 'Switch')


@admin.register(Port)
class PortAdmin(admin.ModelAdmin):
    """A Port is on a Switch or on a Tool."""
    list_display = ('switch', 'port_switch', 'backbone', 'port_backbone', 'svlan')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'switch':
            kwargs['queryset'] = Switch.everything.order_by('model', 'id')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


admin.site.register(TopologyShare)
admin.site.register(PermanentCable)


@admin.register(BaselineExtra)
class BaselineExtraAdmin(admin.ModelAdmin):
    """
    The lab managers' extras (api.clean_state): files under /flash and local logins that every
    Cleanup leaves in place and no Inspection counts as Unwanted.
    """
    list_display = ('kind', 'value', 'note')
    list_filter = ('kind',)


class ProjectMemberInline(admin.TabularInline):
    """A Project's members. The Switch worker brings their Switch accounts in step within minutes."""
    model = ProjectMember
    extra = 0
    readonly_fields = ('last_reserved_at',)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Projects (api.projects): archived, never deleted, so an admin may fix an owner or a name here."""
    inlines = [ProjectMemberInline]
    list_display = ('name', 'reference', 'owner', 'created_at', 'archived_at')
    list_filter = (('archived_at', admin.EmptyFieldListFilter),)
    exclude = ('invite_token',)

    def has_delete_permission(self, request, obj=None):
        return False


class SnapshotSwitchInline(admin.TabularInline):
    model = SnapshotSwitch
    extra = 0
    fields = ('switch', 'model', 'aos_release', 'read_at', 'read_error')
    readonly_fields = fields
    can_delete = False


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    """Snapshots (api.snapshots), to look at: BLab takes them, and keeps the last 10 per Project."""
    inlines = [SnapshotSwitchInline]
    list_display = ('project', 'taken_at', 'reason', 'taken_by', 'note')
    list_filter = ('reason',)
    readonly_fields = ('project', 'taken_at', 'taken_by', 'reason', 'links', 'layout')


class RestoreSwitchInline(admin.TabularInline):
    model = RestoreSwitch
    extra = 0
    fields = ('switch', 'state', 'detail', 'warnings', 'free_on', 'updated_at')
    readonly_fields = fields
    can_delete = False


@admin.register(Restore)
class RestoreAdmin(admin.ModelAdmin):
    """Restores (api.restore), to look at; the Switch worker carries them out."""
    inlines = [RestoreSwitchInline]
    list_display = ('project', 'started_at', 'started_by', 'finished_at')
    readonly_fields = ('project', 'snapshot', 'started_by', 'started_at', 'links')
