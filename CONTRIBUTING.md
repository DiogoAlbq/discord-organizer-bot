# Contribuindo para discord-organizer-bot

Obrigado por considerar contribuir!

## Como contribuir

1. Fork o repositório
2. Crie uma branch para sua feature (`git checkout -b feature/nova-feature`)
3. Faça commit das mudanças (`git commit -am 'Adiciona nova feature'`)
4. Push para a branch (`git push origin feature/nova-feature`)
5. Abra um Pull Request

## Padrões de código

- Python 3.11+
- Type hints obrigatórios em `src/organizer/`
- Lint com `ruff check .`
- Testes com `pytest -q`
- Commits convencionais (ex: `feat:`, `fix:`, `docs:`, `refactor:`)

## Setup de desenvolvimento

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install  # opcional
```

## Testes

```bash
pytest -q
ruff check .
```

## Estrutura do projeto

```
src/organizer/      # Código principal
tests/              # Testes unitários
config/             # Configurações de exemplo
scripts/            # Scripts de dev e deploy
```