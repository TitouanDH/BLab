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
path('account/', views.account),
path('switch_password/', views.choose_switch_password),
path('list_user/', views.list_user),
path('list_user/<int:user_id>/', views.list_user_by_id),
path('token/', views.test_token),
path('del_switch/', views.del_switch),
path('list_switch/', views.list_switch),
path('del_port/', views.del_port),
path('list_port/', views.list_port),
path('list_port/<int:switch_id>/', views.list_port_by_switch),
path('reserve/', views.reserve),
path('release/', views.release),
path('renew/', views.renew),
path('list_reservation/', views.list_reservation),
path('connect/', views.connect),
path('disconnect/', views.disconnect),
path('', views.welcome),
path('share_topology/', views.share_topology),
path('list_shared_topologies/', views.list_shared_topologies),
path('unshare_topology/<int:share_id>/', views.unshare_topology),
path('topology/<int:owner_id>/', views.get_topology),
path('topology/<int:owner_id>/layout/', views.topology_layout),
path('lab_status/', views.lab_status),
path('release_check/<int:switch_id>/', views.release_check),
path('recheck/', views.recheck),
path('switch_accounts/', views.list_switch_accounts),
path('my_lab/', views.my_lab),
]

# Projects (BLAB_PROJECTS on; 404 when off)
urlpatterns += [
path('projects/', views.project_list),
path('projects/<int:project_id>/', views.project_detail),
path('projects/<int:project_id>/topology/', views.project_topology),
path('projects/<int:project_id>/layout/', views.project_layout),
path('projects/<int:project_id>/invite/', views.project_invite),
path('projects/<int:project_id>/leave/', views.project_leave),
path('projects/<int:project_id>/members/<int:user_id>/', views.project_member),
path('projects/<int:project_id>/archive/', views.project_archive),
    path('projects/<int:project_id>/switches/', views.project_switches),
path('projects/<int:project_id>/snapshots/', views.project_snapshots),
path('projects/<int:project_id>/snapshots/<int:snapshot_id>/restore/', views.project_restore),
path('join/<str:token>/', views.join),
]
