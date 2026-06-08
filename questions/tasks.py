from celery import shared_task
from django.core.files.base import ContentFile

from . import extraction
from .models import ImportacaoPDF, Questao, QuestaoImagem


@shared_task
def processar_importacao(importacao_id):
    """Processa o PDF de uma importação: extrai questões e imagens."""
    try:
        imp = ImportacaoPDF.objects.select_related('disciplina__prova__user').get(pk=importacao_id)
    except ImportacaoPDF.DoesNotExist:
        return 'importação inexistente'

    imp.status = ImportacaoPDF.Status.PROCESSANDO
    imp.save(update_fields=['status', 'atualizado_em'])

    try:
        with imp.arquivo.open('rb') as fh:
            pdf_bytes = fh.read()

        profile = getattr(imp.disciplina.prova.user, 'profile', None)
        usar_ia = bool(profile and profile.tem_quota(1))
        resultado = extraction.extrair(pdf_bytes, usar_ia=usar_ia, profile=profile)

        ordem = imp.disciplina.questoes.count()
        for qx in resultado.questoes:
            ordem += 1
            questao = Questao.objects.create(
                disciplina=imp.disciplina,
                importacao=imp,
                numero=qx.numero or ordem,
                enunciado_md=qx.enunciado,
                gabarito=qx.gabarito,
                confianca_extracao=qx.confianca,
                ordem=ordem,
                status=Questao.Status.EM_REVISAO,
            )
            for i, png in enumerate(qx.imagens):
                QuestaoImagem.objects.create(
                    questao=questao,
                    ordem=i,
                    imagem=ContentFile(png, name=f'q{questao.pk}_{i}.png'),
                )

        imp.num_questoes = len(resultado.questoes)
        imp.confianca_media = resultado.confianca_media
        imp.usou_ia = resultado.usou_ia
        imp.status = ImportacaoPDF.Status.CONCLUIDO
        imp.save(update_fields=[
            'num_questoes', 'confianca_media', 'usou_ia', 'status', 'atualizado_em',
        ])
        return f'{imp.num_questoes} questões extraídas'
    except Exception as exc:  # noqa: BLE001
        imp.status = ImportacaoPDF.Status.ERRO
        imp.erro = str(exc)[:2000]
        imp.save(update_fields=['status', 'erro', 'atualizado_em'])
        raise
