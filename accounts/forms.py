from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from legal.forms import AceiteLegalMixin

User = get_user_model()


class CadastroForm(AceiteLegalMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "email")
        labels = {
            "username": "Usuário",
            "first_name": "Nome (opcional)",
            "email": "E-mail",
        }
        help_texts = {
            "email": "Usado para recuperar a senha.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
