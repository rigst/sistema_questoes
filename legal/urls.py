from django.urls import path

from . import views

app_name = "legal"

urlpatterns = [
    path("meus-aceites/", views.meus_aceites, name="meus_aceites"),
    path("reaceite/", views.reaceite, name="reaceite"),
    path("<str:tipo>/<str:versao>/", views.versao, name="versao"),
]
