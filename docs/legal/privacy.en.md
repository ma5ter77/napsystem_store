# NAP Store Privacy Policy

**Version 1.0 of 1 November 2026**

> This version is in force and is applied on the service. The text is under review by a lawyer; a new version may be published as a result (see section 17).

---

## 1. General Provisions

1.1. This Privacy Policy (the "Policy") explains which personal data are processed when you use the **NAP Store** online service at https://store.napsystem.com (the "Service"), for what purposes, on what legal basis, to whom they are disclosed, how long they are kept and what rights data subjects have.

1.2. The Service is a marketplace of the NAP SYSTEM brand on which: (a) third-party developers publish digital "apps" — audio programs with binaural beats (meditation, sleep, focus, audiobooks, music); and (b) independent suppliers (and possibly the Operator itself as one of the suppliers) sell hardware — headphones, NAP chairs (vibroacoustic), controllers, sensors, cables and kits.

1.3. **The Service is not a medical service.** Audio programs, questionnaires and measurements are intended for personal wellbeing and relaxation only; they are not used for diagnosis or treatment and do not replace a consultation with a doctor.

1.4. **Data controller (operator):**

| Detail | Value |
|---|---|
| Name | [LEGAL ENTITY NAME], registered in the Republic of Serbia |
| Registration number (MB) | [МБ] |
| Tax number (PIB) | [ПИБ] |
| Address | [АДРЕС] |
| E-mail for requests | [E-MAIL ДЛЯ ОБРАЩЕНИЙ] |
| E-mail for personal data requests | [E-MAIL ПО ПЕРСОНАЛЬНЫМ ДАННЫМ] |
| Phone (if available) | [ТЕЛЕФОН] |

1.5. **Orders paid through YooKassa (ЮKassa).** For orders paid through ЮKassa, the seller is the person named in the order and in the receipt: [РОССИЙСКОЕ ЮРЛИЦО ИЛИ ИП, ОГРН/ОГРНИП, ИНН, АДРЕС]. For the data needed to perform such an order and settle payments, that person may act as an independent controller; its details are shown in the order.

1.6. This Policy applies to all data processed through the Service, including data of buyers, developers, suppliers and website visitors. By using the Service you confirm that you have read this Policy. Consent to the processing of personal data is given in a **separate document** — "Consent to the Processing of Personal Data"; wellbeing data and measurements are processed only under the **separate** "Consent to the Processing of Wellbeing Data and Measurements".

## 2. Applicable Law

2.1. The Operator is registered in the Republic of Serbia, so processing is governed primarily by the **Serbian Law on Personal Data Protection** (Zakon o zaštiti podataka o ličnosti, Official Gazette of the RS No. 87/2018, "ZZPL").

2.2. The **EU General Data Protection Regulation** (Regulation (EU) 2016/679, "GDPR") applies in the cases set out in Article 3(2) GDPR — where the Service offers goods or digital content to persons in the European Union or monitors their behaviour in the EU.

2.3. The **Russian Federal Law No. 152-FZ of 27.07.2006 "On Personal Data"** ("152-FZ", as currently in force, including the requirement, in force since 01.09.2025, that consent be issued separately from other documents) applies to processing of data of users located in the Russian Federation and to orders performed by the Russian seller (clause 1.5).

2.4. Where different laws provide different levels of protection, the Operator applies the stricter requirement to the extent technically and legally possible.

## 3. Definitions

3.1. **Personal data** — any information relating to an identified or identifiable natural person (data subject).

3.2. **Processing** — any operation performed on personal data: collection, recording, organisation, accumulation, storage, rectification, retrieval, use, transfer, anonymisation, blocking (restriction), erasure, destruction.

3.3. **Processor** — a person who processes data on behalf of the Operator.

3.4. **Wellbeing data and measurements** — answers to questionnaires before and after a session (self-reported relaxation, tension, etc.) and heart rate and heart rate variability (HRV) values. The Operator treats them as data concerning health, i.e. as a **special category** of data.

3.5. **Anonymisation** — actions as a result of which data can no longer be attributed to a specific person without additional information.

## 4. Categories of Data Subjects

4.1. The Operator processes data of:

1. **Visitors** of the Service without an account (including guests with a cart).
2. **Users / buyers** — registered persons who listen to and buy apps, take out subscriptions and order hardware.
3. **Recipients** of hardware orders, where the delivery address is for another person.
4. **Developers** — persons (or company representatives) publishing apps.
5. **Suppliers** — persons (or company representatives) selling hardware.
6. **Service staff** with moderator, content editor, fulfillment and administrator roles — as regards their actions in the Service (audit log).
7. Persons contacting the Operator by e-mail (support, claims, data requests).

## 5. Data Processed, Purposes, Legal Bases and Retention

5.1. **Legal basis codes** used in the table:

| Code | Basis | GDPR | ZZPL | 152-FZ |
|---|---|---|---|---|
| **C** | Entering into and performing a contract (terms of use, offer, order) | Art. 6(1)(b) | Art. 12(1)(2) | Art. 6(1)(5) |
| **LO** | Compliance with a legal obligation (accounting, tax, consumer protection) | Art. 6(1)(c) | Art. 12(1)(3) | Art. 6(1)(2) |
| **LI** | Legitimate interest of the Operator: security, fraud prevention, improving the Service through aggregated statistics | Art. 6(1)(f) | Art. 12(1)(6) | Art. 6(1)(7) |
| **CON** | Consent (separate document) | Art. 6(1)(a) | Art. 12(1)(1) | Art. 6(1)(1), Art. 9 |
| **EC** | Separate explicit consent to special-category data | Art. 9(2)(a) | Art. 17(2)(1) | Art. 10(2)(1) |

For users in Russia, account, order and related data are processed on the basis of the separate consent (CON) and contract performance (C).

5.2. **Data inventory:**

| No. | Category | Data | Purpose | Basis | Retention |
|---|---|---|---|---|---|
| 1 | Account | E-mail; password (stored only as a salted hash); display name; interface language; roles; account creation and deletion time | Registration, login, providing Service features, contacting the user | C (for Russia — also CON) | While the account exists; on deletion — anonymisation (section 11) |
| 2 | Sessions and security | Session cookie (random token; only its hash is stored on the server); CSRF cookie; login attempts (stored as SHA-256 of the e-mail, no plaintext); request IDs | Authentication, protection against password guessing and request forgery | C, LI | Session — until logout or session end; login attempts and request IDs — no longer than technical logs |
| 3 | Technical logs | IP address, request IDs in server and nginx logs | Operation and security, investigating failures and incidents | LI | Up to [90] days |
| 4 | Audit log | Who did what and when in the Service (immutable log) | Security, accountability of staff and user actions, dispute resolution | LI, LO | For the retention period of the related orders and financial records (as a rule 5–10 years); after account deletion linked only to the anonymised profile |
| 5 | Digital content purchases | App, price, status, dates, refunds | Providing access and licence, refunds, accounting | C, LO | The period required by law for accounting records (as a rule 5–10 years) |
| 6 | Subscriptions | Plan, period, status (incl. "past due"), renewal, cancellation | Providing access, auto-renewal, cancellation | C, LO | As item 5 |
| 7 | Hardware orders and delivery | Items, prices; shipping address: name, company, address lines, city, region, postal code, phone; optional Latin transliteration for international labels; parcels, tracking events, returns | Order performance, delivery, returns and warranty, accounting | C, LO | As item 5 |
| 8 | Address book | Saved shipping addresses | Convenience for repeat orders | C | Until the user deletes the address or the account |
| 9 | Payment data | Payment status, payment provider references. **The Operator never receives or stores card numbers** — they are entered on the provider's page | Payment confirmation, refunds, accounting | C, LO | As item 5 |
| 10 | Reviews and ratings | Text, rating, display name (public), helpful votes, reports, moderation actions | Publishing reviews, moderation | C, LI | While the review is published; after account deletion the author is shown as "deleted user" |
| 11 | Devices and playback sessions | Registered playback devices (type, capabilities); sessions (app, time, progress, completion); headphone confirmation; left/right channel test result | Playback, resuming where you left off, correct binaural audio | C | While the account exists; after deletion linked only to the anonymised profile |
| 12 | Wellbeing data and measurements | Answers to before/after-session questionnaires; heart rate and HRV (currently simulated, real sensors planned); timestamps; link to the session | Showing users their own dynamics; anonymised aggregate statistics for developers; improving the Service in aggregate | **EC** (only with separate consent) | 365 days by default, then automatic deletion; immediate deletion on consent withdrawal or account deletion |
| 13 | Unique view counter | Nothing is stored on the device. For a guest the server computes a one-off hash of IP address, browser and date and immediately folds it into a probabilistic HyperLogLog counter; the hash and the address are **not stored** | Counting unique views of app pages (in aggregate) | LI | No identifiers kept; only the aggregate counter is stored |
| 14 | Notifications | E-mail, notification content (order status, moderation, refunds) | Informing about contract performance | C | Sending logs — no longer than technical logs |
| 15 | Developer and supplier data | Name / company name; payout account reference (Stripe Connect ID); API keys (stored as hashes); uploaded files | Publishing apps and products, payouts, integrations | C, LO | While the contract is in force; financial data — the period required by law (as a rule 5–10 years) |
| 16 | Requests | E-mail correspondence | Support, handling claims and data requests | C, LO, LI | As long as needed to handle the request and any dispute; order claims — as item 5 |

5.3. Marketing e-mails are currently **not sent**. If they are introduced, it will only be with separate consent and with the option to opt out at any time.

5.4. The Operator **does not sell** personal data, does not use it for advertising profiling and does not share it with advertising networks.

5.5. Providing account data and order data is necessary to enter into and perform the contract; without them, registration and placing an order are not possible. Providing wellbeing data and measurements is voluntary.

## 6. Wellbeing Data and Measurements (Special Category)

6.1. Before/after-session questionnaires and heart rate/HRV measurements are processed **only with separate explicit consent** ("Consent to the Processing of Wellbeing Data and Measurements"), given via a separate toggle that is not pre-selected, in "Profile → Data & consent". Without this consent the Service works fully, except for the questionnaire and measurement features.

6.2. These data are **not used** for diagnosis or treatment, are not shared with employers or insurers, are not sold and are not used for advertising.

6.3. App **developers** see only **aggregated anonymised statistics**; groups of fewer than **5 people** are hidden. Individual data are never shown to developers.

6.4. Retention is **365 days** by default, after which data are deleted automatically. Withdrawing consent in "Profile → Data & consent" immediately deletes all measurements and questionnaire answers (from backups within 14 days).

6.5. Heart rate and HRV values are currently produced by a **simulator**; connecting real sensors is planned. This Policy and the consent cover both sources.

## 7. Cookies and Local Storage

7.1. The Service uses only cookies and local storage entries necessary for its operation. **No advertising trackers or third-party analytics are used.**

| Name | Type | Purpose | Lifetime |
|---|---|---|---|
| `nap_session` | Cookie (httpOnly) | Authentication: random session token; only its hash is stored on the server | Until logout or session end |
| `nap_csrf` | Cookie | Protection against cross-site request forgery (CSRF) | For the session |
| `nap_locale` | Cookie | Selected interface language | 1 year |
| Cart cookie | Cookie | Cart for guests without an account | 30 days |
| Interface language | localStorage | Remembering the language in the browser | Until cleared by the browser |
| Drafts | localStorage | Keeping unsaved drafts (e.g. review text) | Until cleared by the browser |
| Audio file cache | Browser storage | Offline listening to purchased apps in the Service player | Until cleared by the browser or removed in the player |

7.2. You can delete cookies and local storage in your browser settings. Disabling the `nap_session` and `nap_csrf` cookies makes login impossible.

## 8. Recipients and Processors

8.1. The Operator discloses data only to the extent necessary for the relevant purpose:

| Recipient | Role | Data | Purpose |
|---|---|---|---|
| [ХОСТИНГ-ПРОВАЙДЕР], server located in: [СТРАНА РАЗМЕЩЕНИЯ СЕРВЕРА] | Processor | All data stored in the Service | Hosting, storage, backups |
| Stripe Payments Europe Ltd (Ireland) / Stripe, Inc. (USA) | Processor or independent controller, depending on the provider agreement | Payment data, e-mail, amount; card data is entered directly with Stripe | Payments, refunds, subscriptions; in future — developer payouts (Stripe Connect) |
| ООО НКО «ЮMoney» / ЮKassa (Russia) | Processor or independent controller, depending on the agreement | Payment data, amount, receipt data | Payments in Russia, refunds, receipts |
| [РОССИЙСКОЕ ЮРЛИЦО ИЛИ ИП, ОГРН/ОГРНИП, ИНН, АДРЕС] | Seller for orders paid through ЮKassa | Order and buyer data | Order performance, settlement, accounting |
| CDEK (СДЭК), DHL | Independent controllers / processors (to be confirmed by contract terms) | Recipient name, address, phone, parcel data | Delivery |
| EasyPost | Processor | Recipient name, address, phone, parcel data | Shipping rates and labels |
| [ПОЧТОВЫЙ СЕРВИС] | Processor | E-mail, notification content | E-mail delivery |
| Hardware suppliers | Independent controllers for their own order | Shipping data and items of their order | Shipping goods, returns, warranty |
| App developers | — | **No personal data of buyers are disclosed.** Only aggregated statistics (groups < 5 hidden) and review texts with display names are visible | App analytics, replying to reviews |
| Public authorities | Independent controllers | To the extent of a lawful request | Compliance with law |

8.2. The Operator concludes contracts (data processing agreements) with processors requiring them to ensure confidentiality and security and processing only on the Operator's instructions.

## 9. Cross-Border Transfers

9.1. Depending on the provider, data may be transferred between Serbia, EU Member States, the USA and Russia (for example, payment data to Ireland and the USA via Stripe or to Russia via ЮKassa; shipping data to carriers along the shipping route).

9.2. Transfers from Serbia and the EU rely on the safeguards provided by ZZPL (Articles 63–65) and Chapter V GDPR: adequacy decisions where available and otherwise **standard contractual clauses** (including those adopted by the European Commission and by the Serbian Commissioner). A copy of the applicable safeguards may be requested at [E-MAIL ПО ПЕРСОНАЛЬНЫМ ДАННЫМ].

9.3. Cross-border transfers of data of users in Russia are carried out in accordance with Article 12 of 152-FZ.

## 10. Retention

10.1. Data are kept no longer than necessary for the purposes listed in section 5:

- account data — while the account exists;
- orders, payments, accounting records and the financial journal — for the period required by accounting and tax law (as a rule 5–10 years);
- wellbeing data and measurements — 365 days by default or until consent is withdrawn;
- technical logs — up to [90] days;
- database backups — 14 days (rolling), then overwritten;
- reviews — while published; after account deletion shown as from a "deleted user".

10.2. When the period expires, data are deleted or anonymised. Data deleted from the main database disappear from backups within 14 days at the latest.

## 11. Account Deletion

11.1. You can delete your account in your profile or by sending a request to [E-MAIL ПО ПЕРСОНАЛЬНЫМ ДАННЫМ].

11.2. Account deletion means **anonymisation of the profile**:

- **deleted:** e-mail, password (hash), display name, saved addresses, all wellbeing data and measurements;
- **kept:** orders (including the recipient details they contain), payments, accounting records and the financial journal — to the extent and for the period required by tax and accounting law (as a rule 5–10 years) and for defence against claims; their link to the profile is anonymised; audit log entries, playback sessions and reviews — linked only to the anonymised profile (reviews are shown as from a "deleted user").

11.3. After account deletion, access to purchased apps and subscriptions ends. We recommend cancelling active subscriptions before deletion.

## 12. Data Subject Rights

12.1. Under applicable law you have the right to:

1. **be informed** about processing and **access** your data (obtain a copy);
2. request **rectification** of inaccurate or incomplete data;
3. request **erasure** ("right to be forgotten") where there is no lawful ground to keep the data;
4. request **restriction (blocking)** of processing;
5. receive your data in a structured, machine-readable format (**portability**);
6. **object** to processing based on legitimate interest;
7. **withdraw consent** at any time — without affecting the lawfulness of processing before withdrawal;
8. lodge a **complaint** with a supervisory authority (section 18).

12.2. **How to exercise your rights.** Send a request to [E-MAIL ПО ПЕРСОНАЛЬНЫМ ДАННЫМ] from the e-mail address linked to your account, or use the profile features (editing data, deleting addresses, "Profile → Data & consent", account deletion). To protect your data, the Operator may ask you to confirm your identity.

12.3. **Response deadlines:**

| Regime | Deadline |
|---|---|
| GDPR | Within **one month**; may be extended by two further months where necessary, with notice |
| ZZPL | Within **30 days**; may be extended by a further 60 days, with notice |
| 152-FZ (information and access requests) | **10 working days**; may be extended by no more than **5 working days** with a reasoned notice |
| 152-FZ (rectification of inaccurate data) | 7 working days from receipt of the information |
| 152-FZ (consent withdrawal) | Cessation of processing and destruction of data within 30 days, unless another legal ground applies |

12.4. Exercising rights is free of charge. Manifestly unfounded or excessive requests may be refused to the extent permitted by law, with reasons given.

## 13. Security Measures

13.1. The Operator applies organisational and technical measures, including:

- encrypted connections (TLS);
- passwords stored only as salted hashes;
- httpOnly cookies; protection against cross-site request forgery (CSRF);
- rate limits, including on login attempts;
- server-side role-based access control;
- private file storage with access via signed links;
- immutable audit log and financial journal;
- daily verified backups;
- API keys and session identifiers stored only as hashes.

13.2. In the event of a personal data breach, the Operator notifies supervisory authorities and, where required, affected users within the deadlines set by applicable law.

## 14. Children

14.1. The Service is intended for persons **aged 18 and over**. Persons under 18 may use the Service only through a parent's (legal guardian's) account and under their supervision.

14.2. If the Operator learns that an account has been created by a person under 18 without a parent's involvement, the account may be deleted.

## 15. Automated Decision-Making

15.1. The Operator **does not take decisions producing legal effects** or similarly significantly affecting users based solely on automated processing, including profiling. Automatic technical measures (e.g. a temporary lock after repeated failed login attempts) serve security purposes only.

## 16. Third-Party Services

16.1. When paying, you are redirected to the payment provider's page, and when tracking a parcel possibly to the carrier's website. Their processing is governed by their own policies.

## 17. Changes to the Policy

17.1. The Operator may amend this Policy. A new version is published on this page with its number and date. Registered users are notified of material changes by e-mail or in the Service before they take effect. If a change requires new consent, it will be requested separately.

## 18. Contacts and Supervisory Authorities

18.1. **Operator:** [LEGAL ENTITY NAME], [АДРЕС]; personal data e-mail: [E-MAIL ПО ПЕРСОНАЛЬНЫМ ДАННЫМ]; general requests: [E-MAIL ДЛЯ ОБРАЩЕНИЙ]; phone: [ТЕЛЕФОН].

18.2. **Supervisory authorities:**

- **Serbia:** Commissioner for Information of Public Importance and Personal Data Protection (Poverenik za informacije od javnog značaja i zaštitu podataka o ličnosti), Belgrade.
- **EU:** the national data protection authority of the EU Member State of your habitual residence, place of work or place of the alleged infringement.
- **Russia:** Federal Service for Supervision of Communications, Information Technology and Mass Media (Roskomnadzor).

18.3. We recommend contacting the Operator first — we will try to resolve the issue quickly.

---

## Notes for the lawyer (remove after review)

1. **Localisation (152-FZ Art. 18(5)).** Server country — [СТРАНА РАЗМЕЩЕНИЯ СЕРВЕРА]. If data of users in Russia are first recorded outside Russia, this conflicts with the localisation requirement. Decision needed: Russian server for initial recording / separate Russian stack / assessment of whether 152-FZ applies to the Serbian operator (site targeting Russia: Russian language, roubles, ЮKassa, delivery to Russia).
2. **Roskomnadzor notifications: processing (Art. 22)** and **cross-border transfer (Art. 12)** — to be filed before processing/transfer starts; check who files (Serbian entity and/or the Russian seller under clause 1.5).
3. **Russian seller for ЮKassa orders.** Define the role of [РОССИЙСКОЕ ЮРЛИЦО ИЛИ ИП, ОГРН/ОГРНИП, ИНН, АДРЕС]: independent, joint controller or processor; whether a processing/joint-controller agreement is needed; data exchange between the Serbian and Russian entities is itself a cross-border transfer.
4. **Written form for health data (152-FZ Art. 10(2)(1), Art. 9(4)).** A separate non-pre-ticked checkbox may not satisfy the written-form requirement. Consider a simple electronic signature (63-FZ Art. 6(2)) with an e-mail confirmation code and an agreement recognising it in the terms of use. Also confirm qualification of self-reported scores and heart rate/HRV as health data (conservative approach taken).
5. **Publication of display names in reviews** — separate consent to dissemination (152-FZ Art. 10.1) on the first review — not yet implemented.
6. **E-mail service** — [ПОЧТОВЫЙ СЕРВИС] not yet chosen; once chosen add country, DPA and transfer safeguards.
7. **Hosting** — [ХОСТИНГ-ПРОВАЙДЕР] not yet chosen; DPA and safeguards.
8. **Technical log retention** — [90] days is provisional; confirm. Not separately defined in the facts: retention of the audit log (text ties it to financial records), login attempts, support correspondence, playback sessions and devices after account deletion (text keeps them linked to the anonymised profile; check whether they should be deleted instead).
9. **Roles of Stripe, ЮKassa, CDEK, DHL, EasyPost** (processor vs independent controller) — stated provisionally; confirm against the contracts. Hosting countries of EasyPost, CDEK and DHL are not in the facts.
10. **Transfer safeguards.** Check the current Serbian Government list of adequate countries (ZZPL Art. 64), use of the Commissioner's SCCs for transfers to the USA and Russia, Stripe, Inc.'s status under the EU–U.S. Data Privacy Framework; for Russia — Roskomnadzor's list of countries with adequate protection.
11. **EU representative (Art. 27 GDPR).** If the Service targets users in the EU (Art. 3(2)(a)), the Serbian operator will most likely need to appoint an EU representative. A Serbian representative (ZZPL Art. 44) is not needed because the operator is a Serbian entity. EU representative (Art. 27 GDPR): [ПРЕДСТАВИТЕЛЬ В ЕС] — where users in the EU are targeted.
12. **DPO (ZZPL Art. 56, GDPR Art. 37).** Assess whether processing of wellbeing data is a "core activity on a large scale"; if so, appoint a DPO and publish their contact.
13. **Age threshold.** 18+ adopted (younger only via a parent's account). For reference: ZZPL Art. 16 — 15 years for information society services; GDPR Art. 8 — 16 by default (13–16 under national law); Russia — 14–18 with parental consent. Is age verification needed?
14. **Unique view counter.** Implemented without storing anything on the device (no cookie, no localStorage), so in our assessment the cookie-consent rules (Art. 5(3) Directive 2002/58/EC) do not apply; the IP address is processed momentarily on the basis of legitimate interest and not stored. Please confirm.
15. **152-FZ deadlines** for rectification (7 working days) and consent withdrawal (30 days) were included per Art. 21; confirm they are current.
16. **Breach notification** — Russia: Art. 21(3.1) procedure (24 hours / 72 hours); EU/Serbia: 72 hours to the supervisory authority. An internal procedure is needed.
17. **Recipients in the personal data consent.** Full names and addresses of recipients (Stripe Payments Europe Ltd, Stripe, Inc., ООО НКО «ЮMoney» (YooMoney), CDEK, DHL, EasyPost) — add them to the consent to the processing of personal data or link to a maintained list.
