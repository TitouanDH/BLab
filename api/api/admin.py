from django.contrib import admin
from .models import Switch, Reservation, Port, TopologyShare

# Register your models here.
admin.site.register(Switch)
admin.site.register(Reservation)
admin.site.register(Port)
admin.site.register(TopologyShare)