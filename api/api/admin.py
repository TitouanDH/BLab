from django.contrib import admin
from .models import PermanentCable, Port, Reservation, Switch, SwitchEvent, TopologyShare


class PermanentCableInline(admin.TabularInline):
    """Ports an admin marks as permanently cabled: an Inspection doesn't count them as Unwanted cables."""
    model = PermanentCable
    extra = 0


@admin.register(Switch)
class SwitchAdmin(admin.ModelAdmin):
    inlines = [PermanentCableInline]


@admin.register(SwitchEvent)
class SwitchEventAdmin(admin.ModelAdmin):
    list_display = ('switch', 'kind', 'at', 'ok')
    list_filter = ('kind', 'ok')


admin.site.register(Reservation)
admin.site.register(Port)
admin.site.register(TopologyShare)
admin.site.register(PermanentCable)
