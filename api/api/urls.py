"""
URL configuration for api project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path
from . import views

urlpatterns = [
path('signup/', views.signup),
path('login/', views.login),
path('logout/', views.logout),
path('list_user/', views.list_user),
path('list_user/<int:user_id>/', views.list_user_by_id),
path('token/', views.test_token),
path('add_switch/', views.add_switch),
path('del_switch/', views.del_switch),
path('list_switch/', views.list_switch),
path('add_port/', views.add_port),
path('del_port/', views.del_port),
path('list_port/', views.list_port),
path('list_port/<int:switch_id>/', views.list_port_by_switch),
path('reserve/', views.reserve),
path('release/', views.release),
path('list_reservation/', views.list_reservation),
path('connect/', views.connect),
path('disconnect/', views.disconnect),
path('', views.welcome),
]