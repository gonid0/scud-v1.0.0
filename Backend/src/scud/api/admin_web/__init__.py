"""Admin web panel: server-rendered HTML using Jinja2 + HTMX + Tailwind (CDN).

Архитектура:
  - Никакого build step. Tailwind/HTMX подгружаются с CDN — это сознательный
    trade-off: deployment остаётся `pip install + uvicorn`, не нужно вешать
    node/npm в Dockerfile.
  - Все шаблоны в templates/ относительно этого пакета (см. config.TEMPLATES).
  - Все экшены (создание/удаление/revoke) — POST из <form>; редирект через
    303 See Other на parent-страницу.
  - HTMX используется для inline-обновлений (фильтры в таблицах,
    confirmation-модалок).
  - Аутентификация — отдельная dependency get_admin_web_user (cookie-сессия,
    подписанная itsdangerous), которая внутри транслирует cookie → ApiKey row.
"""
