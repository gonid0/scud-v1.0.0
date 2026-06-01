# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/) · версии по [SemVer](https://semver.org/).

## [Unreleased]

### Changed
- **`permit.max_total_issued` — смена семантики гейта.** Раньше лимит считал ВСЕ когда-либо
  выпущенные ключи пропуска (любой статус, всё время) — пожизненный потолок. Теперь считает
  только ещё-не-истёкшие ключи (`expires_at > now`, любой статус, включая отозванные пока их
  срок не вышел). Цель — защита bloom-фильтра отзыва от раздувания: истёкшие ключи ридер
  отбрасывает по сроку и в фильтр не вносит, поэтому они освобождают слот в квоте. Имя колонки
  сохранено ради совместимости. Backend: `count_total_keys` → `count_unexpired_keys(now)`,
  код ошибки `total_issued_exceeded` → `unexpired_cap_exceeded`. Android: локальный счётчик
  «X / лимит» и гейт кнопки выпуска приведены к той же семантике (подпись «Действующих / Valid»).

## [1.0.0] — 2026-06-01

Первый функционально завершённый релиз самостоятельно-хостируемой офлайн-СКУД.

### Состав релиза
- **Протокол (`docs/00`):** байт-точный криптопротокол (Ed25519/X25519/ChaCha20-Poly1305, domain-separated),
  сверяемый золотым корпусом векторов (`docs/test_vectors/protocol_v1.json`) в трёх независимых реализациях.
- **Backend:** полный REST (app + admin) + админ-веб, жизненный цикл пользователей/пропусков/ключей с
  двухфазным отзывом, адаптивная генерация per-reader bloom-фильтров, курьерская доставка, учёт проходов,
  webhooks (HMAC), наблюдаемость. Multi-account (сессии), permit `max_total_issued`. Phase-1 серверная
  параметризация ридеров (профили/overrides/bounds/резолвер + `config-script`). Миграции 0001–0008.
- **ESP32 firmware:** все inner-операции протокола (ACCESS/FDI/TIME_SYNC/REVOKE/BLACKLIST/FILTER_UPDATE/
  HANDOVER/PASSAGE); NFC end-to-end; BLE-канал (NimBLE); NFC↔BLE handover; потоковая заливка больших
  фильтров во flash; параметризация через NVS + `SET-*`; `esp_task_wdt`-backstop; intermittent BLE-adv.
- **Android:** HCE (NFC) + BLE, роль носителя ключа и офлайн-курьера; синхронизация с сервером, учёт
  ключей/пропусков/задач, multi-account.
- **Desktop:** провижнер ридеров (.NET MAUI) — локальные шаблоны и серверные профили (resolved `config-script`).
- **Эксплуатация:** Docker-compose (dev/prod), `.env.example`, миграции, gu'd по развёртыванию (`docs/04` §2A)
  и интеграции в существующие системы (`docs/12`).

### Известные ограничения / Roadmap
- **Батарейный/автономный режим — не реализован** (единственный явно открытый пункт). Ридер работает в
  always-on профиле. Финальная архитектура энергосбережения (HAL `INfcFrontend`/`IRtc`, драйвер **CLRC663**
  с true LPCD, light/deep-sleep, power-профили) спроектирована в `docs/13_power_hal_architecture.md`
  (roadmap RM-0…RM-8) и запланирована к реализации после защиты.

[1.0.0]: https://github.com/gonid0/VKR/releases/tag/v1.0.0
