from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.db.models.functions import Length
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_safe

from ai.models import TextoTopico
from questions.models import LeituraTopico, Questao, RespostaRevisao, Topico

from .models import Disciplina, Prova


@login_required
@require_safe
def dashboard(request):
    provas = (
        Prova.objects.filter(user=request.user)
        .annotate(n_disciplinas=Count("disciplinas", distinct=True))
        .prefetch_related("disciplinas")
    )
    total_questoes = Questao.objects.filter(disciplina__prova__user=request.user).count()
    total_concluidas = Questao.objects.filter(
        disciplina__prova__user=request.user, status=Questao.Status.CONCLUIDA
    ).count()
    total_topicos = Topico.objects.filter(disciplina__prova__user=request.user).count()
    total_lidos = LeituraTopico.objects.filter(
        user=request.user, topico__disciplina__prova__user=request.user
    ).count()

    # Atalho "continuar lendo": o último tópico aberto, desde que ainda
    # exista e ainda não tenha sido marcado como lido.
    perfil = getattr(request.user, "profile", None)
    continuar = getattr(perfil, "ultimo_topico", None) if perfil else None
    if (
        continuar is not None
        and LeituraTopico.objects.filter(
            user=request.user,
            topico=continuar,
        ).exists()
    ):
        continuar = None

    # Quanto ainda falta ler, em tempo: é o número que responde "dá para
    # encaixar isso hoje?", que a contagem de tópicos sozinha não responde.
    chars_a_ler = (
        TextoTopico.objects.filter(
            topico__disciplina__prova__user=request.user,
            status=TextoTopico.Status.CONCLUIDO,
        )
        .exclude(
            topico__in=LeituraTopico.objects.filter(user=request.user).values("topico"),
        )
        .aggregate(s=Sum(Length("texto_md")))["s"]
        or 0
    )
    # ~900 caracteres por minuto: leitura de estudo, mais lenta que a corrida.
    minutos_a_ler = round(chars_a_ler / 900)
    if minutos_a_ler >= 60:
        horas, resto = divmod(minutos_a_ler, 60)
        tempo_a_ler = f"{horas}h{resto:02d}" if resto else f"{horas}h"
    else:
        tempo_a_ler = f"{minutos_a_ler}min"

    # "14h de leitura" assusta e não diz o que fazer hoje; a mesma conta em
    # dias a uma hora por dia vira um plano.
    META_DIARIA_MIN = 60
    dias_no_ritmo = -(-minutos_a_ler // META_DIARIA_MIN) if minutos_a_ler else 0

    # Desempenho nas revisões: acerto acumulado do usuário.
    respostas = RespostaRevisao.objects.filter(user=request.user)
    total_respostas = respostas.count()
    total_acertos = respostas.filter(correta=True).count()
    pct_acerto = round(total_acertos / total_respostas * 100) if total_respostas else 0

    contexto = {
        "provas": provas,
        "total_provas": provas.count(),
        "total_disciplinas": Disciplina.objects.filter(prova__user=request.user).count(),
        "total_questoes": total_questoes,
        "total_concluidas": total_concluidas,
        "total_topicos": total_topicos,
        "total_lidos": total_lidos,
        "total_a_ler": total_topicos - total_lidos,
        "pct_lidos": round(total_lidos / total_topicos * 100) if total_topicos else 0,
        "pode_revisar": total_lidos > 0,
        "total_respostas": total_respostas,
        "total_acertos": total_acertos,
        "pct_acerto": pct_acerto,
        "continuar": continuar,
        "minutos_a_ler": minutos_a_ler,
        "tempo_a_ler": tempo_a_ler,
        "dias_no_ritmo": dias_no_ritmo,
        "na_fila": Questao.objects.filter(
            disciplina__prova__user=request.user,
            status__in=[Questao.Status.NA_FILA, Questao.Status.PROCESSANDO],
        ).count(),
    }
    return render(request, "exams/dashboard.html", contexto)


@login_required
def prova_excluir_inline(request, pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    prova = get_object_or_404(Prova, pk=pk, user=request.user)
    prova.delete()
    return JsonResponse({"ok": True})


@login_required
def disciplina_excluir_inline(request, pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    disciplina = get_object_or_404(Disciplina, pk=pk, prova__user=request.user)
    disciplina.delete()
    return JsonResponse({"ok": True})


@login_required
def prova_renomear_inline(request, pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    prova = get_object_or_404(Prova, pk=pk, user=request.user)
    nome = request.POST.get("nome", "").strip()
    if not nome:
        return JsonResponse({"error": "Nome obrigatório."}, status=400)
    prova.nome = nome
    prova.save(update_fields=["nome"])
    return JsonResponse({"pk": prova.pk, "nome": prova.nome})


@login_required
def disciplina_renomear_inline(request, pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    disciplina = get_object_or_404(Disciplina, pk=pk, prova__user=request.user)
    nome = request.POST.get("nome", "").strip()
    if not nome:
        return JsonResponse({"error": "Nome obrigatório."}, status=400)
    disciplina.nome = nome
    disciplina.save(update_fields=["nome"])
    return JsonResponse({"pk": disciplina.pk, "nome": disciplina.nome})


@login_required
def prova_criar_inline(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    nome = request.POST.get("nome", "").strip()
    if not nome:
        return JsonResponse({"error": "Nome obrigatório."}, status=400)
    prova = Prova(user=request.user, nome=nome)
    prova.save()
    return JsonResponse({"pk": prova.pk, "nome": prova.nome})


@login_required
def disciplina_criar_inline(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    nome = request.POST.get("nome", "").strip()
    prova_pk = request.POST.get("prova_pk")
    if not nome:
        return JsonResponse({"error": "Nome obrigatório."}, status=400)
    prova = get_object_or_404(Prova, pk=prova_pk, user=request.user)
    disc = Disciplina(prova=prova, nome=nome)
    disc.save()
    return JsonResponse(
        {
            "pk": disc.pk,
            "nome": disc.nome,
            "url": reverse("questions:disciplina", args=[disc.pk]),
        }
    )
