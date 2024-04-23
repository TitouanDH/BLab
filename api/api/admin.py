from django.contrib import admin
from .models import Switch, Reservation, Port

# Register your models here.
admin.site.register(Switch)
admin.site.register(Reservation)
admin.site.register(Port)