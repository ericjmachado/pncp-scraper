# Termos do seu perfil (empresa de software). Edite à vontade — a busca ignora acentos.
TERMOS = [
    "software",
    "softwares",
    "sistema informatizado",
    "sistemas informatizados",
    "sistema de gestao",
    "sistema web",
    "aplicativo",
    "aplicativos",
    "licenciamento de software",
    "licencas de software",
    "desenvolvimento de sistema",
    "desenvolvimento de sistemas",
    "desenvolvimento de software",
    "desenvolvimento web",
    "fabrica de software",
    "tecnologia da informacao",
    "solucao tecnologica",
    "plataforma digital",
    "portal web",
    "website",
    "hospedagem de site",
    "computacao em nuvem",
    "servicos de informatica",
    "manutencao de sistemas",
    "suporte tecnico em ti",
]


def fts_query_perfil():
    return " OR ".join(f'"{t}"' for t in TERMOS)


def fts_query_usuario(q):
    """Cada palavra vira um termo com prefixo ("desenv"* acha "desenvolvimento")."""
    tokens = [t.replace('"', '""') for t in q.split()]
    return " ".join(f'"{t}"*' for t in tokens)
