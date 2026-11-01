# Stripe: США и мировой рынок (NAP-010, NAP-012, NAP-020)

Stripe принимает оплату покупателей вне России: карты, Apple Pay, Google Pay, Link — на защищённой странице
Stripe Checkout (данные карт к нам не попадают). Покупатели из России — через ЮKassa (отдельная задача).

## Кто продавец

Счёт Stripe открывается **на компанию в стране, где работает Stripe** (например, американская компания,
которой принадлежит napsystem.com). Российское юрлицо Stripe не принимает. Если продавцом для мира будет
американская компания, а для России — российская, это нужно отразить в оферте и политике (два оператора
данных, передача данных иностранных покупателей в США). Вопрос к юристу — см. NAP-062.

## Что уже умеет магазин

| Возможность | Как |
|---|---|
| Разовая покупка, подписка | Checkout Session (`payment` / `subscription`), ключи идемпотентности |
| Доступ только после подтверждения | webhook с проверкой подписи → inbox → право; redirect ничего не подтверждает |
| Один клиент Stripe на пользователя | `users.payment_customer_ref`: карты, подписки, счета в одном месте |
| Кабинет «Карты и счета» | Stripe Billing Portal (`POST /api/v1/billing/portal`), кнопка в профиле → «Подписки» |
| Налоги (Stripe Tax) | `NAP_STRIPE_AUTOMATIC_TAX=true`: цены без налога, налог добавляется при оплате; цифровое — по адресу плательщика, оборудование — по адресу доставки; коды налога: цифровые услуги, товары, доставка |
| Налог в учёте | налог проводится в `tax:payable` отдельно; доли 30/70 считаются без налога; расхождение суммы фиксируется в аудите |
| Счета компаний | `NAP_STRIPE_TAX_ID_COLLECTION=true`: покупатель вводит VAT/tax ID |
| Возвраты | Refund API + webhook `refund.*` |
| Выплаты авторам | Stripe Connect Express: автор нажимает «Подключить выплаты» в «Финансах», Stripe собирает его данные; переводы (transfers) на его счёт. В Stripe → Connect нужно включить Express и указать название платформы |
| Оборудование | строки «товары» и «доставка», адрес доставки передаётся в Stripe для налога |

## Что сделать владельцу

1. **Аккаунт Stripe** на компанию-продавца: dashboard.stripe.com. Тестовый режим работает сразу, без проверки
   документов.
2. **Тестовый ключ** (Developers → API keys → Secret key, начинается с `sk_test_`) → GitHub:
   ma5ter77/napsystem_store → Settings → Secrets and variables → Actions → Secrets → `STRIPE_TEST_SECRET_KEY`.
   После этого workflow **stripe-sandbox** проверяет на настоящем Stripe: разовую покупку, подписку,
   отклонённую карту, возврат, заказ оборудования с адресом в США, портал. Ключ никому не пересылать.
3. **Налоги (по желанию сразу, обязательно до продаж):** Stripe → Tax → регистрации (штаты США, где есть
   обязанность собирать налог; EU OSS для НДС ЕС; Великобритания и т. д.). Затем переменная GitHub
   `STRIPE_TAX=true` — проверка начнёт ждать налог в заказе.
4. **Боевой запуск** (после проверки и решения юриста):
   - в `shared/.env` на сервере: `NAP_PAYMENT_PROVIDER=stripe`, `NAP_STRIPE_SECRET_KEY=sk_live_…`,
     `NAP_STRIPE_WEBHOOK_SECRET=whsec_…`, `NAP_STRIPE_AUTOMATIC_TAX=true`;
   - Stripe → Developers → Webhooks → endpoint `https://store.napsystem.com/api/v1/webhooks/payments/stripe`,
     события: `checkout.session.completed`, `checkout.session.expired`, `checkout.session.async_payment_succeeded`,
     `checkout.session.async_payment_failed`, `invoice.paid`, `invoice.payment_failed`,
     `customer.subscription.deleted`, `refund.created`, `refund.updated`, `charge.refund.updated`;
   - Stripe → Settings → Billing → Customer portal: разрешить смену карты, просмотр счетов, отмену подписки;
   - Settings → Payment methods: карты, Apple Pay, Google Pay, Link.

## Валюты (NAP-012)

Автор задаёт цену в евро и/или долларах (по одной на вид цены и валюту; подписка — один период для всех валют).
Покупатель видит и платит в своей валюте: явный выбор внизу страницы (cookie `nap_currency`), иначе по региону
браузера (en-US → USD, остальные → EUR). Язык интерфейса цены не меняет. Если цены в его валюте нет — показывается и списывается цена автора.
Список валют — `NAP_CURRENCIES` (по умолчанию `["EUR","USD"]`). Оборудование пока продаётся в валюте поставщика.

## Дальше

- ЮKassa для России (NAP-012).
