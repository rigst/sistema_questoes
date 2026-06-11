from django.urls import path

from . import views

app_name = 'exams'

urlpatterns = [
    path('', views.provas, name='provas'),
    path('nova/', views.prova_form, name='prova_nova'),
    path('<int:pk>/', views.prova_detalhe, name='prova_detalhe'),
    path('<int:pk>/editar/', views.prova_form, name='prova_editar'),
    path('<int:pk>/excluir/', views.prova_excluir, name='prova_excluir'),
    path('<int:prova_pk>/disciplina/nova/', views.disciplina_form, name='disciplina_nova'),
    path('disciplina/<int:pk>/editar/', views.disciplina_form, name='disciplina_editar'),
    path('disciplina/<int:pk>/excluir/', views.disciplina_excluir, name='disciplina_excluir'),
    path('criar-inline/', views.prova_criar_inline, name='prova_criar_inline'),
    path('<int:pk>/renomear-inline/', views.prova_renomear_inline, name='prova_renomear_inline'),
    path('<int:pk>/excluir-inline/', views.prova_excluir_inline, name='prova_excluir_inline'),
    path('disciplina/criar-inline/', views.disciplina_criar_inline, name='disciplina_criar_inline'),
    path('disciplina/<int:pk>/renomear-inline/', views.disciplina_renomear_inline, name='disciplina_renomear_inline'),
    path('disciplina/<int:pk>/excluir-inline/', views.disciplina_excluir_inline, name='disciplina_excluir_inline'),
]
