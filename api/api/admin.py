from django.contrib import admin
from django.utils import timezone

from .quarantine import lift
from .models import (PendingCleanup, PermanentCable, Port, Quarantine, Reservation, Switch, SwitchEvent,
                     TopologyShare)


class PermanentCableInline(admin.TabularInline):
    """Ports an admin marks as permanently cabled: an Inspection doesn't count them as Unwanted cables."""
    model = PermanentCable
    extra = 0


@admin.register(Switch)
class SwitchAdmin(admin.ModelAdmin):
    """Out of service is set here: a reason takes the Switch out of reservation, emptying it puts it back."""
    inlines = [PermanentCableInline]
    list_display = ('mngt_IP', 'model', 'out_of_service_reason')
    readonly_fields = ('out_of_service_since',)

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


@admin.register(SwitchEvent)
class SwitchEventAdmin(admin.ModelAdmin):
    list_display = ('switch', 'kind', 'at', 'ok')
    list_filter = ('kind', 'ok')


@admin.register(Quarantine)
class QuarantineAdmin(admin.ModelAdmin):
    """An admin lifts the Quarantines that name nobody, or any other when it has to be."""
    list_display = ('switch', 'holder', 'opened_at', 'lifted_at')
    list_filter = (('lifted_at', admin.EmptyFieldListFilter),)
    actions = ['lift']

    @admin.action(description='Lift the selected Quarantines')
    def lift(self, request, queryset):
        for quarantine in queryset.filter(lifted_at__isnull=True):
            lift(quarantine, request.user, f'lifted by {request.user.username} (admin)')


@admin.register(PendingCleanup)
class PendingCleanupAdmin(admin.ModelAdmin):
    list_display = ('switch', 'holder', 'requested_at', 'started_at', 'give_up_at')


admin.site.register(Reservation)
admin.site.register(Port)
admin.site.register(TopologyShare)
admin.site.register(PermanentCable)
