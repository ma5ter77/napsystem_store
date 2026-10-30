# Отчёт по критериям приёмки

Окружение проверки: Linux x86_64, 2 vCPU (Intel Xeon 2,1 ГГц), 7,8 ГБ RAM; PostgreSQL 16, Redis 7, FFmpeg 6.1.1 (soxr), Python 3.13.18, Node.js 22. Дата прогона: 31.10.2026.

Статусы: **passed** — проверено автоматически в этой поставке; **failed** — не выполнено; **not run** — не запускалось (причина указана).

Итог: **passed 35, failed 0, not run 3** (21 — Docker Compose; 23 и 33 — частично, live-sandbox Stripe/EasyPost; их офлайн-части пройдены).

| № | Критерий | Статус | Доказательство |
|---|---|---|---|
| 1 | Моно/стерео 44,1 и 48 кГц; повреждённые и пустые отклоняются | passed | `test_dsp.py::test_formats_mono_stereo_44_48` (4 комбинации), `test_compressed_inputs` (FLAC/MP3/M4A), `test_real_format_checked_by_content`, `test_corrupt_empty_and_multichannel_rejected` (пустой, только заголовок, испорченный FLAC, обрезанный WAV, 6 каналов) |
| 2 | Слой 250/260 Гц, разница 10 Гц, допуск ≤ 0,1 Гц | passed | `test_layer_frequencies_and_separation`; метод в `docs/dsp.md`; demo QA: 250,0/260,0/10,0 (`docs/qa/demo-conversions-qa.json`) |
| 3 | Противоположная несущая подавлена ≥ 60 dB | passed | то же; измерено > 180 dB на изолированном слое |
| 4 | Поблочно = непрерывно ≤ 1e-6, без сбросов фазы | passed | `test_blockwise_equals_continuous_reference` (случайные блоки, замкнутая формула, огибающая 0 на краях) |
| 5 | Число отсчётов, нет NaN/Inf/клиппинга, TP ≤ −1 dBTP | passed | `test_formats_*`, `test_true_peak_ceiling_with_loud_source` (общий gain, повторное измерение мастера, допуск 0,2 dB) |
| 6 | Слой выключен → исходник сохранён | passed | `test_layer_disabled_preserves_source` (≤ 2 LSB 24 бит) |
| 7 | Отмена/сбой/retry без частичного результата и двойной ревизии | passed | `test_jobs.py`: отмена в процессе, гонка отмены и завершения, падение worker (lease), идемпотентное создание, повреждённый файл без авто-повтора |
| 8 | Настоящий файл → конвертация → приложение → модерация → публикация | passed | `test_flow_digital.py::test_real_file_to_publication_and_purchase`; браузерный E2E RU/EN (`frontend/e2e/flows.cjs`, `docs/qa/e2e-run.txt`) |
| 9 | Покупка открывает библиотеку и сеанс; неоплативший не получает файл | passed | там же: `no_entitlement` до оплаты, redirect без webhook не даёт доступ, FLAC выдаётся после оплаты |
| 10 | Дубли, конкуренция, иной порядок webhooks; неверная подпись | passed | `test_billing.py::test_webhook_duplicates_reorder_concurrency_and_bad_signature`, `test_out_of_order_refund_before_payment_is_retried` |
| 11 | 10 000 → 3 000/7 000; возврат 4 000 → 1 800/4 200; малые возвраты без накопления ошибки | passed | `test_split_examples_exact`, `test_30_70_and_refund_4000_example` |
| 12 | Продление, отмена, отказ, возврат периода | passed | `test_subscription_renew_fail_cancel_and_period_refund` |
| 13 | Чужие материалы, финансы, измерения недоступны | passed | `test_isolation_between_developers_and_users`, изоляция продавцов в `test_hardware.py`, сеансы чужих → 404 |
| 14 | Подмена файла/manifest/профиля не обходит модерацию; сеанс закреплён за версией | passed | `test_substitution_cannot_bypass_moderation` (заморозка, повторная загрузка 410, подмена объекта и manifest → `integrity_failed`), grant содержит `version_id` |
| 15 | Браузер не подменяет цену/роль/entitlement; истёкшая ссылка требует нового разрешения | passed | цена из сервера при `amount_minor` в запросе; роли только у админа; `test_signed_link_expires_and_needs_new_grant`; CSRF-тест |
| 16 | Несовместимая capability блокирует запуск с причиной | passed | `test_sessions.py::test_incompatible_capability_blocks_start_with_reason` |
| 17 | Повтор команд не дублирует; второй сеанс устройства отклоняется | passed | `test_commands_idempotent_and_one_session_per_device` (уникальный частичный индекс в БД) |
| 18 | L/R сохраняется; пользовательский тест ≠ аппаратная изоляция | passed | `test_web_headphones_lr_test_is_user_confirmation_not_measurement`, отсутствие даунмикса/перестановки в grant и плеере, DSP-тест моно → L=R |
| 19 | Сеанс и остановка работают без интернета; события синхронизируются один раз | passed | API: `test_offline_buffered_events_sync_once_and_local_stop`; браузер: E2E отключает сеть во время воспроизведения, локальная остановка, синхронизация после восстановления |
| 20 | Прерванный сеанс без вымышленных измерений; пропуск анкеты; HRV ≠ ЭЭГ | passed | `test_questionnaire_skip_consent_and_no_invented_measurements` |
| 21 | Чистый запуск поднимает сервисы, миграции и demo без внешних секретов | **not run** (Docker) | `docker compose up` не запускался: из этой среды закрыт Docker Hub. Эквивалент проверен: пустая БД → `alembic upgrade head` → `python -m app.seed` (реальные конвертации, mock-оплата и tracking) без секретов; это же делает каждый прогон тестов |
| 22 | SDK-пример работает через API, ключ не в браузере | passed | `sdk/examples/create-draft-and-convert.ts` выполнен против API + Celery (черновик, загрузка, конвертация 250/260/10 Гц); SDK отказывает с `napk_` в браузере; ключ хешируется, управлять ключами можно только из cookie-сессии |
| 23 | Контрактные тесты платёжного адаптера; live sandbox при ключах | passed / **not run** | контракт Mock и Stripe (подпись, нормализация) — passed; `test_stripe_live_sandbox_checkout` — not run (нет `NAP_STRIPE_SECRET_KEY`) |
| 24 | Перезапуск сохраняет данные; восстановление БД проверено | passed | перезапуск PostgreSQL и API — данные совпадают; `ops/restore_verify.sh`: совпадение счётчиков и суммы журнала, триггеры неизменяемости на месте (`docs/operations.md`) |
| 25 | Полный BOM, имеющееся/недостающее, совместимость | passed | `test_bom_owned_missing_and_compatibility`, `test_compatible_hardware_for_app_and_speakers_not_binaural`; E2E «Добавить недостающее» |
| 26 | Аппаратный checkout → передача поставщику; чужой продавец не видит | passed | `test_multi_seller_cart_creates_independent_orders_and_isolation`; E2E RU/EN |
| 27 | Последняя единица не продаётся дважды; резервы/allocated/поздняя оплата; многопродавцовые заказы независимы | passed | `test_last_unit_cannot_be_sold_twice_concurrently` (4 параллельных покупателя), `test_reservation_expiry_and_late_payment`, `test_late_payment_without_stock_is_explicit_exception` |
| 28 | Нет фиктивной бесплатной доставки; пересчёт; индивидуальное предложение | passed | `test_no_free_shipping_on_errors_and_recalculation`, `test_oversize_chair_custom_quote_flow` |
| 29 | Несколько посылок; этикетка ≠ delivered; общий статус по всем | passed | `test_parcels_labels_tracking_and_status_aggregation`; demo-заказ с двумя посылками |
| 30 | Timeout не создаёт вторую этикетку; дубли tracking; источник статуса | passed | там же (симуляция потерянного ответа, счётчик покупок = 1; дубль события; источники simulator/supplier) |
| 31 | Возвраты ≤ оплаты; склад после приёмки; цифровые права отдельно | passed | `test_returns_restock_only_after_inspection_and_refund_limits` |
| 32 | Оборудование без цифровой комиссии 30/70; сумма и документы по снимку | passed | `test_hardware_sale_creates_no_developer_share`; упаковочный лист из снимка, HS-коды не выдумываются |
| 33 | Mock и ручная логистика без ключей; контракт test-адаптера; документы закрыты | passed / **not run** | mock + ручной режим — passed; контракт EasyPost (HMAC, нормализация, крупногабарит) — passed; live EasyPost — not run (нет ключа) |
| 34 | Оба сквозных сценария на RU и EN | passed | `frontend/e2e/flows.cjs`: 12 шагов × 2 языка, подписи кнопок берутся из словарей |
| 35 | Переключение языка сохраняет карточку, сессию, корзины; URL/SSR/lang/hreflang | passed | E2E-шаг переключения (путь, корзина, `<html lang>`); SSR-разметка: `canonical`, `hreflang` ru/en/x-default |
| 36 | Отсутствующий перевод помечен; язык аудио независим; поиск и фильтры | passed | `test_missing_translation_is_marked`, `test_locale_changes_texts_not_prices_or_stock`, `test_search_and_filters_both_languages` |
| 37 | Локаль не меняет цену/валюту/остатки; адреса ≥ 3 стран | passed | `test_locale_changes_texts_not_prices_or_stock`; `test_addresses_three_countries_unicode_and_latin_rule` (US, AE, RU, DE, GB) |
| 38 | Стабильные коды API, локализованные тексты/уведомления; пропуски словарей выявляются автоматически | passed | `test_csrf_and_locale_of_errors`, `test_every_error_code_used_in_code_is_translated`, `test_dictionaries_complete_and_plural_forms`, `test_order_locale_used_for_notifications`; `npm run check-i18n` (674 ключа × 2) |

## Дополнительно проверено

- S3-хранилище (загрузка, скачивание, Range) — на S3-совместимом эмуляторе moto (`test_s3_storage.py`); MinIO в compose — not run.
- Конвертация через настоящий Celery-воркер и Redis (SDK-пример, очередь `media`).
- TypeScript: `tsc --noEmit` для фронтенда и SDK; сборка `next build`.

## Найдено и исправлено при проверке

- Проигрыватель отправлял вторую команду подряд с устаревшей версией состояния → `version_conflict` (исправлено, покрыто E2E).
- Кнопка «Купить», нажатая до загрузки профиля, отправляла вошедшего пользователя на вход (исправлено).
- Анкета «после» требовала сети после обрыва связи (определения анкет загружаются заранее).
- Бесплатное получение не фиксировало транзакцию (исправлено, покрыто тестами сеансов).
- Измерение памяти в benchmark учитывало синтез входного файла (исправлено, замер в отдельном процессе).

Симуляторы не подтверждают работу реального кресла, выплаты, доставки или изменение мозговой активности.
