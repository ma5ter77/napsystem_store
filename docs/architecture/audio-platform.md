# Аудиоплатформа NAP System — аудит и архитектура интеграции (ТЗ v2.1, M0)

Документ этапа **M0** (NAP-099). Основа для M1–M6. Правило ТЗ: дорабатываем существующую платформу, а не
строим отдельное приложение; рабочие функции, данные, авторизация и API-стиль сохраняются.

## 1. Что уже есть (аудит репозитория ma5ter77/napsystem_store, 01.11.2026)

| Слой | Сейчас | Что из этого берём |
|---|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2, Alembic (миграции 0001–0010), PostgreSQL 16, Celery + Redis, OpenAPI `/api/v1` | всё: новые маршруты `/api/v1/audio/*` в том же стиле (ошибки, идемпотентность, rate limit, аудит) |
| Auth / RBAC | cookie-сессии + CSRF, API-ключи со scope, роли listener/developer/seller/moderator/admin/content_editor/fulfillment, проверка на backend | авторы студии = роль `developer`; проверка владельца на каждом объекте |
| Хранилище | приватное (локальный том или S3/SeaweedFS), ключи генерирует сервер, короткоживущие подписанные ссылки, `Asset` с SHA-256 и флагом `immutable`, upload-тикеты с проверкой размера и хэша | исходники студии — `Asset` нового вида `studio_source` (неизменяемые, без привязки к приложению) |
| Аудио-DSP | `app/dsp`: декодирование ffmpeg + soxr, блочная обработка, генератор двух несущих с непрерывной фазой и кривыми частоты (NAP-021), true peak 4×, LUFS BS.1770, 24-бит мастер, побитовая проверка FLAC-доставки; `modes.py`/`variants.py` (NAP-063): SSB-сдвиг 2.0, 5.1, 6-канальный WAV EXTENSIBLE, пилот-тоны | измерители, генератор несущих, запись WAV EXTENSIBLE, декодер — переиспользуются в рендере студии |
| Задания | `ConversionJob`: lease, попытки, отмена, восстановление, inline-режим для тестов | тот же шаблон для `RenderJob` |
| Магазин | приложения, версии, программы из сеансов, manifest с хэшем и обнаружением подмены, модерация, каталог, отзывы, оборудование, корзина | M3 публикует проекты студии как версии приложений |
| Оплаты | Stripe (мир, налоги, портал, Connect-выплаты), ЮKassa (выключена), подписки, возвраты, журнал проводок, доли 30/70, inbox/outbox webhooks | основа M4 (подписка на каталог, права, роялти) |
| Плеер | веб-плеер сеансов, офлайн-остановка и синхронизация, адаптеры устройств (симулятор кресла) | M2/M5 |
| Тесты | 170+ pytest (реальный ffmpeg), E2E Playwright RU/EN, axe, Docker-стенд выкладки | новые тесты T01–T11 в `tests/test_audio_*.py` |
| Развёртывание | GHCR-образы, napdeploy.py с откатом, Hetzner/Ubuntu (NAP-087/097) | без изменений |

**Вывод о готовности:** проект готов к интеграции студии без смены стека. Тяжёлый рендер — в Celery-воркере
(`worker-media`, лимит памяти уже задан), хранение — существующее приватное хранилище, UI — Next.js.

## 2. Решения

1. **Модель проекта — JSON-состояние + неизменяемые ревизии.** `AudioProject.state` (дорожки, клипы, генераторы)
   валидируется pydantic-схемой; каждое сохранение увеличивает `revision` (оптимистическая блокировка), снимок
   ревизии хранится в `audio_project_revisions` с SHA-256 канонического JSON. Рендер всегда ссылается на ревизию.
   Сущности ТЗ (TimelineTrack, AudioClip, GeneratorNode) — элементы схемы, а не отдельные таблицы: так проще
   undo/redo, автосохранение и воспроизводимость; отдельные таблицы — `RoutingProfile`, `RenderJob`, `RenderArtifact`.
2. **Время — целые отсчёты** на мастер-частоте проекта (`start_frame`, `source_offset_frame`, `duration_frames`).
3. **Один DSP для preview и экспорта.** Preview — это тот же серверный рендер окна (≤ 60 с) теми же функциями;
   браузер только проигрывает его через один `AudioContext`. Отдельного JS-DSP нет, расхождений нет. WASM-ядро —
   при необходимости в M2+ с conformance-фикстурами.
4. **float64 суммирование, детерминизм.** Без неявных gain/EQ/нормализации/лимитера/SRC; ресэмплинг только если
   частота исходника ≠ частоте проекта (soxr precision 28) и записывается в отчёт. Клиппинг суммы — ошибка рендера,
   жёсткое ограничение — только с явным согласием в проекте (`on_clip: "hard_clip"`), с подсчётом в отчёте.
5. **Шины и профили.** Логические шины NEURO_LEFT/RIGHT, VOICE_CENTER, MUSIC_LEFT/RIGHT, LFE, BACK_LEFT/RIGHT;
   профили HEADPHONES_2_0, NAP_ACOUSTIC_3_0 (это 3.0, не 2.1), NAP_CHAIR_5_1, NAP_PRO_7_1 — версионируемые матрицы
   «шина × выходной канал» с явной маской каналов WAV (2.0 = 0x3, 3.0 = 0x7, 5.1(side) = 0x60F, 7.1 = 0x63F).
   Порядок PCM определяется маской WAVE_FORMAT_EXTENSIBLE, а не текстом ТЗ.
6. **Проверка результата независимым reader** (stdlib `wave` + ffprobe): кадры, каналы, частота, раскладка,
   пики/true peak/RMS по каналам, «тихие» каналы, SHA-256.
7. **Честные статусы.** В UI показывается реальное число выходов `AudioContext.destination.maxChannelCount`;
   экспорт 5.1/7.1 отделён от физического воспроизведения; bit-perfect в браузере не заявляется.

## 3. Карта интеграции (M1)

```
frontend/app/[locale]/developer/studio       список проектов, создание, библиотека исходников (с правами)
frontend/app/[locale]/developer/studio/[id]  редактор: шины-дорожки, клипы с waveform, генераторы, инспектор,
                                             zoom/snap, undo/redo, автосохранение, preview, рендер и скачивание
frontend/components/studio/                  model.ts (чистые операции, unit-тесты), Editor, Sources, Render, Waveform
backend/app/audio/schema.py             схема состояния проекта (pydantic), шины, профили
backend/app/audio/generators.py         binaural / isochronic с непрерывной фазой и автоматизацией
backend/app/audio/render.py             детерминированный блочный рендер ревизии → WAV, отчёт и проверка
backend/app/services/audio_projects.py  проекты, ревизии, ассеты студии, задания рендера (lease/retry)
backend/app/api/audio.py                /api/v1/audio/* (assets, projects, revisions, renders, render-jobs, routing-profiles, preview)
backend/app/models/audio.py             AudioProject, AudioProjectRevision, RoutingProfile, RenderJob, RenderArtifact
backend/migrations/versions/0011_*      таблицы студии, вид ассета studio_source/studio_render
backend/tests/test_audio_studio.py      T01–T09, API, preview = export, права доступа, восстановление после падения
frontend/e2e/studio.cjs                 браузерный сценарий целиком + axe на редакторе (в CI)
```

## 4. Риски

| Риск | Мера |
|---|---|
| 120-минутные 8-канальные рендеры по памяти | блочный рендер (65 536 кадров), исходники декодируются во float64-spool на диске (`numpy.memmap`) |
| Браузер даунмиксит/ресэмплит | preview помечается частотой и раскладкой `AudioContext`; экспорт — источник истины |
| > 4 ГиБ WAV (8 каналов × 24 бит × 48 кГц ≈ 1,15 ГБ/ч) | M1: лимит 3 ч 8-канального рендера; RF64 — в M2 (TODO) |
| Права на исходники | `rights_metadata` у ассета обязателен при загрузке (источник, лицензия); проверка — M3 |
| Конфликт с NAP-063 (5.1 с ритмом во фронте) | v2.1 главнее: раскладка задаётся профилем; движок NAP-063 переиспользуется |
| Защита контента | M5: .napx, AES-256-GCM по сегментам, лицензии; до этого подписчики получают только защищённые ссылки, не мастер |

## 5. Миграции БД

0011 — `audio_projects`, `audio_project_revisions`, `routing_profiles` (+ 4 встроенных профиля v1),
`render_jobs`, `render_artifacts`; расширение `assets.kind`: `studio_source`, `studio_render`.
Дальше: M2 `device_calibrations`; M3 `content_rights`, статусы публикации; M4 `entitlement_rights`, `devices`,
`license_leases`, `royalty_events`; M5 ключи/пакеты.

## 6. План этапов и TODO

- **M1 (NAP-100, выполнен в ветке codex/audio-studio-m1):** см. §3 и docs/audio-studio.md. Проверено: 18 backend-тестов
  (T01–T09 и сценарии API), 8 unit-тестов модели редактора, браузерный E2E с проверкой отчёта 5.1 и WCAG 2.1 AA.
  Не входит в M1 и перенесено: RF64 (> 4 ГиБ) и FLAC-экспорт → M2; T03 покрывает все 4 профиля, но матрица
  с пользовательскими gain — M2; автоматизация несущей в UI (в схеме и рендере есть, в инспекторе только Δ) → M2.
- **M2 (NAP-101) TODO:** UI Channel Router (матрица gain, mute, polarity, delay, meters), пользовательские профили,
  автоматические рендеры всех профилей, калибровка выходов с подтверждением динамика и bleed-тестом, capability
  detection, RF64/W64 > 4 ГиБ, FLAC-экспорт 6/8 каналов, 5.1.2/7.1.4/N, T03 (6/8 каналов), T10.
- **M3 (NAP-102) TODO:** публикация проекта как контента, workflow draft → technical_validation → rights_review →
  moderation → published → archived, правообладатели и территории, библиотека, T18.
- **M4 (NAP-103) TODO:** подписка на каталог (уровни), права stream/offline/commercial/raw_export, устройства и
  лимиты сессий, защищённые stream-сессии, роялти по квалифицированным прослушиваниям, T12, T15–T17, T19, T20.
- **M5 (NAP-104), этап 1 выполнен (ветка claude/secure-player):** .napx (сегментированный AES-256-GCM, envelope
  encryption, подписанный manifest), ключи устройств P-256, подписанные офлайн-лицензии, API, чтение в браузере,
  T13, T14 — см. docs/secure-player.md (миграция 0023). TODO: этап 2 — сохранение без интернета в плеере и устройства
  в профиле; нативный вывод и калибровка, T21–T23 — вместе с NAP-066.
- **M6 (NAP-105) TODO:** нагрузка, безопасность, audio-QA, мониторинг, бэкапы, руководство оператора, выпуск, T11.
