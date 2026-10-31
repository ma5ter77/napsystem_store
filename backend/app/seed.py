"""Idempotent demo data. All prices, compatibility, tariffs and measurements are explicitly TEST data.
Audio is synthesized here (own material) and processed by the real converter."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import timedelta

import numpy as np
from sqlalchemy import select

from .config import get_settings
from .db import session_scope, utcnow
from .dsp.pipeline import _wav_header
from .models import (App, Asset, Bom, BomItem, Category, Compatibility, ConversionJob, Device, DeviceModel,
                     DspProfile, Inventory, Price, Product, Questionnaire, RoleGrant, Seller, SellerMember, Sku, User,
                     Warehouse)
from .security import hash_password
from .services import apps as appsvc
from .services.common import upsert_translation
from .services.conversion import profile_snapshot, run_job
from .storage import get_storage, new_key

PASSWORD = "demo-password-1"
APP_CATS = [("meditation", "Медитации", "Meditation"), ("audiobooks", "Аудиокниги", "Audiobooks"),
            ("music", "Музыка", "Music"), ("education", "Обучение", "Education"), ("sleep", "Сон", "Sleep")]
HW_CATS = [("chairs", "Кресла NAP SYSTEM", "NAP SYSTEM chairs"),
           ("vibroacoustic", "Акустика и виброакустика", "Acoustic & vibroacoustic"),
           ("headphones", "Наушники", "Headphones"), ("controllers", "Контроллеры и интерфейсы", "Controllers & interfaces"),
           ("sensors", "Датчики", "Sensors"), ("power", "Блоки питания", "Power supplies"),
           ("cables", "Кабели и переходники", "Cables & adapters")]
PROFILES = [
    ("alpha-demo-v1", {"ru": "Альфа (демо, 10 Гц)", "en": "Alpha (demo, 10 Hz)"},
     {"ru": "Инженерный профиль: 250/260 Гц, разница 10 Гц, исходник −6 dB, тон −30 dBFS. Технические параметры "
            "сигнала, а не обещание состояния.",
      "en": "Engineering profile: 250/260 Hz, 10 Hz difference, source −6 dB, tone −30 dBFS. Signal parameters, "
            "not a promise of any state."},
     {"carrier_center_hz": 255.0, "beat_hz": 10.0, "source_gain_db": -6.0, "tone_gain_dbfs": -30.0,
      "fade_in_seconds": 0.5, "fade_out_seconds": 0.5}),
    ("theta-demo-v1", {"ru": "Тета (демо, 6 Гц)", "en": "Theta (demo, 6 Hz)"},
     {"ru": "197/203 Гц, разница 6 Гц.", "en": "197/203 Hz, 6 Hz difference."},
     {"carrier_center_hz": 200.0, "beat_hz": 6.0, "source_gain_db": -6.0, "tone_gain_dbfs": -32.0,
      "fade_in_seconds": 2.0, "fade_out_seconds": 2.0}),
    # NAP-080, parameters set by the customer on 31.10.2026: pure sines 200 Hz (L) / 240 Hz (R), 40 Hz difference,
    # strict stereo (no channel mixing). Pending methodologist sign-off (NAP-061).
    ("gamma-40-v1", {"ru": "Гамма (40 Гц)", "en": "Gamma (40 Hz)"},
     {"ru": "Чистые синусоиды: левый канал 200 Гц, правый 240 Гц, разница 40 Гц. Каналы не смешиваются — нужны "
            "наушники. Технические параметры сигнала, а не обещание состояния.",
      "en": "Pure sine waves: left 200 Hz, right 240 Hz, 40 Hz difference. Channels are never mixed — headphones "
            "required. Technical signal parameters, not a promise of any state."},
     {"carrier_center_hz": 220.0, "beat_hz": 40.0, "source_gain_db": -6.0, "tone_gain_dbfs": -32.0,
      "fade_in_seconds": 3.0, "fade_out_seconds": 3.0}),
    ("beta-demo-v1", {"ru": "Бета (демо, 14 Гц)", "en": "Beta (demo, 14 Hz)"},
     {"ru": "293/307 Гц, разница 14 Гц.", "en": "293/307 Hz, 14 Hz difference."},
     {"carrier_center_hz": 300.0, "beat_hz": 14.0, "source_gain_db": -6.0, "tone_gain_dbfs": -30.0,
      "fade_in_seconds": 1.0, "fade_out_seconds": 1.0}),
]
QUESTIONNAIRE = [
    {"id": "relaxation", "min": 0, "max": 10, "labels": {"ru": "Расслабленность", "en": "Relaxation"},
     "low": {"ru": "совсем нет", "en": "not at all"}, "high": {"ru": "полностью", "en": "completely"}},
    {"id": "tension", "min": 0, "max": 10, "labels": {"ru": "Напряжение", "en": "Tension"},
     "low": {"ru": "нет", "en": "none"}, "high": {"ru": "сильное", "en": "strong"}},
    {"id": "alertness", "min": 0, "max": 10, "labels": {"ru": "Бодрость", "en": "Alertness"},
     "low": {"ru": "сонливость", "en": "drowsy"}, "high": {"ru": "очень бодро", "en": "very alert"}},
]


def synth(path: str, seconds: float, fs: int, channels: int, seed: int, base: float) -> None:
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    t = np.arange(n) / fs
    lfo = 0.6 + 0.4 * np.sin(2 * np.pi * 0.07 * t)
    pad = sum(np.sin(2 * np.pi * base * r * t + rng.uniform(0, 6)) * a for r, a in ((1, .5), (1.25, .3), (1.5, .25), (2, .12)))
    noise = np.convolve(rng.standard_normal(n), np.ones(64) / 64, mode="same") * 0.8
    sig = 0.18 * lfo * (pad + noise)
    env = np.minimum(1, np.minimum(t / 2.0, (seconds - t) / 2.0))
    sig *= env
    data = sig[:, None] if channels == 1 else np.stack([sig, np.roll(sig, int(fs * 0.011)) * 0.97], axis=1)
    q = np.clip(np.rint(data * 32767), -32768, 32767).astype("<i2")
    with open(path, "wb") as fh:
        fh.write(_wav_header(n, channels, 16, fs))
        fh.write(q.tobytes())


def user(db, email, name, roles, locale="ru") -> User:
    u = db.scalar(select(User).where(User.email == email))
    if u:
        return u
    u = User(email=email, password_hash=hash_password(PASSWORD), display_name=name, locale=locale)
    db.add(u)
    db.flush()
    for r in roles:
        db.add(RoleGrant(user_id=u.id, role=r))
    return u


def cat(db, kind, slug, ru, en, pos) -> Category:
    c = db.scalar(select(Category).where(Category.kind == kind, Category.slug == slug))
    if not c:
        c = Category(kind=kind, slug=slug, names={"ru": ru, "en": en}, position=pos)
        db.add(c)
        db.flush()
    return c


def product(db, seller, wh, category, slug, brand, model, tr, sku_rows, **kw) -> Product:
    p = db.scalar(select(Product).where(Product.slug == slug))
    if p:
        return p
    p = Product(seller_id=seller.id, category_id=category.id, slug=slug, brand=brand, model=model, warehouse_id=wh.id,
                status="published", destinations=kw.pop("destinations", []), **kw)
    db.add(p)
    db.flush()
    for loc in ("ru", "en"):
        upsert_translation(db, "product", p.id, loc, tr[loc], "published")
    for row in sku_rows:
        stock = row.pop("stock", 0)
        s = Sku(product_id=p.id, **row)
        db.add(s)
        db.flush()
        if not p.made_to_order:
            db.add(Inventory(sku_id=s.id, warehouse_id=wh.id, on_hand=stock))
    db.flush()
    db.refresh(p)
    return p


def T(ru_title, en_title, ru_sum, en_sum, ru_desc="", en_desc="", in_box=None, ret=None):
    in_box = in_box or ([], [])
    ret = ret or ("Возврат 14 дней с момента получения, товар в исходной упаковке. Демо-условия.",
                  "14-day return from receipt, original packaging. Demo terms.")
    return {"ru": {"title": ru_title, "summary": ru_sum, "description": ru_desc or ru_sum, "in_box": in_box[0],
                   "return_terms": ret[0], "delivery_terms": "DAP: импортные пошлины и налоги оплачивает получатель; "
                                                             "их сумма заранее неизвестна."},
            "en": {"title": en_title, "summary": en_sum, "description": en_desc or en_sum, "in_box": in_box[1],
                   "return_terms": ret[1], "delivery_terms": "DAP: import duties and taxes are paid by the recipient; "
                                                             "the amount is not known in advance."}}


def sku(code, price, w, dims, origin="DE", provides=None, hs=None, desc=None, battery="none", pkg=1, stock=0):
    return {"code": code, "price_minor": price, "currency": "EUR", "weight_g": w, "length_mm": dims[0],
            "width_mm": dims[1], "height_mm": dims[2], "origin_country": origin, "provides": provides or [],
            "hs_code": hs, "customs_description": desc, "contains_battery": battery, "package_count": pkg,
            "stock": stock}


def compat(db, s: Sku, feature, status, source, dm=None, cfg=None, details=None):
    db.add(Compatibility(sku_id=s.id, device_model_id=dm.id if dm else None, feature=feature, status=status,
                         source=source, tested_config=cfg, verified_at=utcnow() if status == "verified" else None,
                         details=details or {}))


def make_app(db, dev, slug, category, author, meta, lang, price, sessions_spec, profile_slug, state="published",
             kit=None, preview=True):
    if db.scalar(select(App).where(App.slug == slug)):
        return
    app = appsvc.create_app(db, dev, slug, category, author, meta, lang)
    app.is_demo = True
    db.flush()
    v = appsvc.latest_version(db, app.id)
    prof = db.scalar(select(DspProfile).where(DspProfile.slug == profile_slug))
    jobs = []
    for i, (key, titles, seconds, fs, ch, base) in enumerate(sessions_spec):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        synth(path, seconds, fs, ch, hash(slug + key) % 1000, base)
        data = open(path, "rb").read()
        a = Asset(owner_id=dev.id, app_id=app.id, kind="source", storage_key=new_key("source", dev.id, "wav"),
                  original_name=f"{key}.wav", content_type="audio/wav", size_bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest(), status="uploaded")
        get_storage().put_file(a.storage_key, path, "audio/wav")
        os.remove(path)
        db.add(a)
        db.flush()
        j = ConversionJob(owner_id=dev.id, app_id=app.id, source_asset_id=a.id, profile_id=prof.id,
                          profile_snapshot=profile_snapshot(prof))
        db.add(j)
        db.flush()
        jobs.append((key, titles, j.id))
    db.commit()
    for _, _, jid in jobs:
        assert run_job(jid) == "completed", f"seed conversion failed for {slug}"
    db.expire_all()
    v = db.get(type(v), v.id)
    app = db.get(App, app.id)
    appsvc.update_version(db, app, v, {"confirm_rights": True}, dev.id)
    appsvc.set_program(db, app, v, [{"key": k, "titles": t, "conversion_job_id": str(j)} for k, t, j in jobs], dev.id)
    if price:
        appsvc.set_price(db, app, *price, actor_id=dev.id)
    if kit:
        app.recommended_kit_product_id = kit.id
    db.flush()
    if state == "draft":
        return app
    appsvc.submit(db, app, v, dev.id)
    if state == "submitted":
        return app
    mod = db.scalar(select(User).where(User.email == "moderator@nap.demo"))
    if state == "rejected":
        appsvc.moderate(db, v, mod.id, "reject", "Демо: уточните описание и права на фоновую музыку. / Demo: clarify "
                                                 "the description and background music rights.")
        return app
    appsvc.moderate(db, v, mod.id, "approve", "OK")
    appsvc.publish(db, app, v, dev.id)
    if preview:
        app.public_preview_asset_id = uuid.UUID(db.get(ConversionJob, jobs[0][2]).result["preview"])
    return app


def run() -> None:
    s = get_settings()
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == "admin@nap.demo")):
            seed_reviews(db)  # added in 0.3.0: existing demo databases get the demo ratings too
            admin = db.scalar(select(User).where(User.email == "admin@nap.demo"))
            for slug, names, desc, params in PROFILES:  # profiles added in later releases (gamma-40-v1)
                if not db.scalar(select(DspProfile).where(DspProfile.slug == slug)):
                    db.add(DspProfile(slug=slug, version=1, names=names, descriptions=desc, params=params,
                                      created_by=admin.id))
            print("demo data already present")
            return
        admin = user(db, "admin@nap.demo", "Admin (demo)", ["user", "admin", "moderator"])
        user(db, "moderator@nap.demo", "Moderator (demo)", ["user", "moderator"])
        dev1 = user(db, "dev1@nap.demo", "Studio Aurora (demo)", ["user", "developer"])
        dev2 = user(db, "dev2@nap.demo", "Second Developer (demo)", ["user", "developer"], "en")
        buyer = user(db, "user@nap.demo", "Demo User", ["user"])
        user(db, "buyer.en@nap.demo", "Demo Buyer EN", ["user"], "en")
        s1 = user(db, "seller@nap.demo", "Operator seller (demo)", ["user", "seller"])
        f1 = user(db, "fulfillment@nap.demo", "Fulfillment operator (demo)", ["user", "fulfillment"])
        s2 = user(db, "seller2@nap.demo", "Second supplier (demo)", ["user", "seller"], "en")
        for i, (slug, ru, en) in enumerate(APP_CATS):
            cat(db, "app", slug, ru, en, i)
        hc = {slug: cat(db, "hardware", slug, ru, en, i) for i, (slug, ru, en) in enumerate(HW_CATS)}
        for slug, names, desc, params in PROFILES:
            db.add(DspProfile(slug=slug, version=1, names=names, descriptions=desc, params=params, created_by=admin.id))
        db.add(Questionnaire(key="state-v1", items=QUESTIONNAIRE))
        dm = DeviceModel(code="NAP-SYSTEM-1", name="NAP SYSTEM chair (model data pending)", api_version="1.0",
                         firmware="unknown", capabilities={"note": "protocol documentation not available"},
                         documented=False)
        db.add(dm)
        op = Seller(name="NAP Operator (demo own sale)", kind="operator", country="DE",
                    settlement_policy={"policy": "operator_own_sale", "developer_share": None})
        sup = Seller(name="Aurora Audio Supply (demo)", kind="third_party", country="DE",
                     settlement_policy={"policy": "supplier_settlement_tbd"})
        db.add_all([op, sup])
        db.flush()
        db.add_all([SellerMember(seller_id=op.id, user_id=s1.id, role="seller"),
                    SellerMember(seller_id=op.id, user_id=f1.id, role="fulfillment"),
                    SellerMember(seller_id=sup.id, user_id=s2.id, role="seller")])
        wh1 = Warehouse(seller_id=op.id, name="Berlin demo warehouse", country="DE",
                        address={"name": "NAP Demo", "line1": "Musterstraße 1", "city": "Berlin", "postal_code": "10115",
                                 "phone": "+49 30 000000"})
        wh2 = Warehouse(seller_id=sup.id, name="Leipzig demo warehouse", country="DE",
                        address={"name": "Aurora Demo", "line1": "Beispielweg 5", "city": "Leipzig",
                                 "postal_code": "04109", "phone": "+49 341 000000"})
        db.add_all([wh1, wh2])
        db.flush()

        chair = product(db, op, wh1, hc["chairs"], "nap-chair-one", "NAP SYSTEM", "Chair One (demo)",
                        T("Кресло NAP Chair One (демо)", "NAP Chair One (demo)",
                          "Нейроакустическое кресло. Крупногабаритный груз — доставка по индивидуальному расчёту.",
                          "Neuroacoustic chair. Oversize freight — shipping by custom quote.",
                          in_box=(["Кресло", "Блок управления", "Кабель питания EU"], ["Chair", "Control unit", "EU power cable"])),
                        [sku("NAP-CH1-GRY", 349000, 68000, (1500, 900, 1100), provides=["vibroacoustic_output", "stereo_output"],
                             desc="Massage/relaxation chair with built-in speakers", stock=3)],
                        freight_mode="custom_quote", prep_days=5, warranty_months=24, assembly_included=True,
                        specs={"power": "230 V AC, EU plug", "audio_inputs": ["3.5 mm", "USB-C"], "weight_kg": 68},
                        hue=28)
        chair_pro = product(db, op, wh1, hc["chairs"], "nap-chair-pro", "NAP SYSTEM", "Chair Pro (demo, made to order)",
                            T("Кресло NAP Chair Pro (демо, под заказ)", "NAP Chair Pro (demo, made to order)",
                              "Изготовление под заказ ~45 дней. Не является складским наличием.",
                              "Made to order in ~45 days. Not shown as stock."),
                            [sku("NAP-CHP-BLK", 529000, 74000, (1550, 950, 1150), provides=["vibroacoustic_output", "stereo_output"])],
                            made_to_order=True, lead_time_days=45, freight_mode="custom_quote", prep_days=5,
                            warranty_months=24, assembly_included=True, hue=12)
        hp = product(db, op, wh1, hc["headphones"], "nap-studio-headphones", "NAP Audio", "Studio HP-1 (demo)",
                     T("Наушники NAP Studio HP-1 (демо)", "NAP Studio HP-1 headphones (demo)",
                       "Закрытые наушники. Проверенный бинауральный тракт с симулятором.",
                       "Closed-back headphones. Binaural path verified with the simulator.",
                       in_box=(["Наушники", "Кабель 3,5 мм"], ["Headphones", "3.5 mm cable"])),
                     [sku("NAP-HP1", 14900, 450, (220, 200, 110), origin="CN", provides=["stereo_output", "stereo_separation"],
                          hs="851830", desc="Headphones", stock=20)], hue=200)
        ctrl = product(db, op, wh1, hc["controllers"], "nap-link-controller", "NAP SYSTEM", "Link Controller (demo)",
                       T("Контроллер NAP Link (демо)", "NAP Link controller (demo)",
                         "Интерфейс между проигрывателем и креслом.", "Interface between player and chair."),
                       [sku("NAP-LINK", 8900, 300, (150, 100, 50), stock=10, desc="Audio interface")], hue=260)
        sensor = product(db, op, wh1, hc["sensors"], "nap-pulse-sensor", "NAP Bio", "Pulse HR-1 (demo)",
                         T("Датчик пульса HR-1 (демо)", "HR-1 pulse sensor (demo)",
                           "Нагрудный датчик пульса. Содержит литиевый аккумулятор — ограничения перевозки.",
                           "Chest heart-rate sensor. Contains a lithium battery — shipping restrictions apply."),
                         [sku("NAP-HR1", 5900, 120, (120, 80, 40), origin="CN", provides=["heart_rate_sensor"],
                              battery="li_ion_contained_in_equipment", desc="Heart rate monitor", stock=5)], hue=340)
        psu = product(db, op, wh1, hc["power"], "nap-psu-24v-eu", "NAP SYSTEM", "PSU 24V EU (demo)",
                      T("Блок питания 24 В, вилка EU (демо)", "24 V power supply, EU plug (demo)",
                        "Для контроллера и кресла. 100–240 В.", "For controller and chair. 100–240 V."),
                      [sku("NAP-PSU24-EU", 3900, 400, (140, 90, 60), stock=15)], hue=90)
        cable = product(db, op, wh1, hc["cables"], "nap-cable-35", "NAP SYSTEM", "Cable 3.5 mm (demo)",
                        T("Кабель 3,5 мм, 2 м (демо)", "3.5 mm cable, 2 m (demo)", "Стереокабель.", "Stereo cable."),
                        [sku("NAP-CBL35", 1200, 60, (100, 80, 20), stock=50)], hue=150)
        vibro = product(db, sup, wh2, hc["vibroacoustic"], "aurora-vibro-pad", "Aurora Audio", "Vibro Pad VP-2 (demo)",
                        T("Виброакустический мат VP-2 (демо)", "VP-2 vibroacoustic pad (demo)",
                          "Последний экземпляр на складе. Виброакустика не заменяет бинауральный тракт.",
                          "Last unit in stock. Vibroacoustics do not replace a binaural path."),
                        [sku("AUR-VP2", 19900, 3500, (700, 450, 120), provides=["vibroacoustic_output"], stock=1)], hue=45)
        hp2 = product(db, sup, wh2, hc["headphones"], "aurora-over-ear", "Aurora Audio", "Over-Ear OE-5 (demo)",
                      T("Наушники Aurora OE-5 (демо)", "Aurora OE-5 headphones (demo)",
                        "Совместимость заявлена поставщиком, не проверена платформой.",
                        "Compatibility claimed by the supplier, not verified by the platform."),
                      [sku("AUR-OE5", 12900, 380, (210, 190, 100), origin="CN", provides=["stereo_output", "stereo_separation"],
                           stock=8)], hue=220)
        speakers = product(db, sup, wh2, hc["vibroacoustic"], "aurora-desk-speakers", "Aurora Audio", "Desk Speakers DS-1 (demo)",
                           T("Колонки Aurora DS-1 (демо)", "Aurora DS-1 speakers (demo)",
                             "Стереоколонки не гарантируют разделение сигналов между ушами.",
                             "Stereo speakers do not guarantee left/right ear separation."),
                           [sku("AUR-DS1", 9900, 2200, (300, 200, 200), provides=["stereo_output"], stock=6)], hue=180)
        kit = product(db, op, wh1, hc["headphones"], "nap-starter-kit", "NAP SYSTEM", "Starter Kit (demo, components)",
                      T("Стартовый комплект NAP (демо, из компонентов)", "NAP Starter Kit (demo, components)",
                        "Собирается из компонентов: наличие определяется деталями.",
                        "Assembled from components: availability is defined by the parts."),
                      [], kind="kit_component", hue=300)
        boxed = product(db, op, wh1, hc["headphones"], "nap-travel-kit-boxed", "NAP SYSTEM", "Travel Kit (demo, boxed)",
                        T("Дорожный комплект NAP (демо, готовый)", "NAP Travel Kit (demo, boxed)",
                          "Готовый складской комплект: собственный SKU и остаток.",
                          "Ready-made boxed kit: own SKU and stock."),
                        [sku("NAP-TRAVEL-BOX", 21900, 1200, (300, 250, 150), provides=["stereo_output", "stereo_separation"],
                             stock=4)], kind="kit_stock", hue=320)
        S = lambda p: p.skus[0]  # noqa
        compat(db, S(hp), "binaural_path", "verified", "NAP lab (demo)", dm, "Web player + simulator, L/R test, 48 kHz")
        compat(db, S(hp2), "binaural_path", "vendor_claimed", "Aurora Audio datasheet")
        compat(db, S(speakers), "binaural_path", "incompatible", "Platform policy: speakers mix both channels at the ears")
        compat(db, S(chair), "vibroacoustic", "vendor_claimed", "NAP SYSTEM (demo)", dm)
        compat(db, S(chair), "binaural_path", "unverified", "Built-in speakers: L/R ear separation not confirmed", dm)
        compat(db, S(vibro), "vibroacoustic", "vendor_claimed", "Aurora Audio datasheet", dm)
        compat(db, S(psu), "power", "verified", "NAP lab (demo)", dm, "230 V EU", {"voltage": "100-240V", "plug": "EU"})
        compat(db, S(cable), "connector", "verified", "NAP lab (demo)", None, None, {"connector": "3.5 mm TRS"})
        compat(db, S(ctrl), "connector", "verified", "NAP lab (demo)", dm, None, {"connectors": ["USB-C", "3.5 mm"]})
        compat(db, S(sensor), "heart_rate", "unverified", "Not yet tested with NAP SYSTEM")
        compat(db, S(boxed), "binaural_path", "verified", "NAP lab (demo)", dm)
        b = Bom(product_id=kit.id, version=1)
        db.add(b)
        db.flush()
        for pos, (sk, role, req, alts) in enumerate([(S(hp), "headphones", True, [str(S(hp2).id)]),
                                                     (S(ctrl), "controller", True, []), (S(cable), "cable", True, []),
                                                     (S(psu), "power", True, []), (S(sensor), "sensor", False, []),
                                                     (S(vibro), "vibroacoustic", False, [])], 1):
            db.add(BomItem(bom_id=b.id, position=pos, sku_id=sk.id, quantity=1, required=req, role=role,
                           alternative_sku_ids=alts))
        bb = Bom(product_id=boxed.id, version=1)
        db.add(bb)
        db.flush()
        db.add(BomItem(bom_id=bb.id, position=1, sku_id=S(hp).id, quantity=1, required=True, role="headphones"))
        db.add(BomItem(bom_id=bb.id, position=2, sku_id=S(cable).id, quantity=1, required=True, role="cable"))
        db.flush()

        make_app(db, dev1, "quiet-harbor", "meditation", "Studio Aurora",
                 {"ru": {"title": "Тихая гавань", "subtitle": "Медитация с бинауральным слоем «Альфа»",
                         "description": "Короткая программа из двух сеансов. Синтезированная музыка демо-студии и "
                                        "технический бинауральный слой 250/260 Гц. Это не медицинская услуга и не "
                                        "обещание определённого состояния."},
                  "en": {"title": "Quiet Harbor", "subtitle": "Meditation with an “Alpha” binaural layer",
                         "description": "A short two-session program. Synthesized demo-studio music plus a technical "
                                        "250/260 Hz binaural layer. Not a medical service and not a promise of any "
                                        "particular state."}}, "none", ("one_time", 499, "EUR", None),
                 [("arrival", {"ru": "Прибытие", "en": "Arrival"}, 40, 44100, 2, 196.0),
                  ("stillness", {"ru": "Тишина", "en": "Stillness"}, 50, 48000, 1, 174.6)], "alpha-demo-v1", kit=kit)
        make_app(db, dev1, "focus-course", "education", "Studio Aurora",
                 {"ru": {"title": "Курс фокуса", "subtitle": "Подписка: занятия для концентрации",
                         "description": "Курс из двух занятий с профилем «Бета (демо)». Подписка на это приложение."},
                  "en": {"title": "Focus Course", "subtitle": "Subscription: sessions for concentration",
                         "description": "A two-lesson course using the “Beta (demo)” profile. Subscription to this app."}},
                 "none", ("subscription", 999, "EUR", "month"),
                 [("lesson-1", {"ru": "Занятие 1", "en": "Lesson 1"}, 35, 48000, 2, 220.0),
                  ("lesson-2", {"ru": "Занятие 2", "en": "Lesson 2"}, 35, 44100, 2, 246.9)], "beta-demo-v1", kit=kit)
        make_app(db, dev2, "night-stories", "sleep", "Second Developer",
                 {"ru": {"title": "Ночные истории", "subtitle": "Бесплатно: звуковые сцены для сна",
                         "description": "Бесплатное приложение. Язык аудио — английский; язык интерфейса от этого не "
                                        "зависит."},
                  "en": {"title": "Night Stories", "subtitle": "Free: soundscapes for sleep",
                         "description": "Free app. Audio language is English; the interface language is independent."}},
                 "en", ("free", 0, "EUR", None),
                 [("harbor-night", {"en": "Harbor at night"}, 30, 48000, 2, 130.8)], "theta-demo-v1")
        make_app(db, dev2, "deep-review", "music", "Second Developer",
                 {"ru": {"title": "На модерации (демо)"}, "en": {"title": "Under review (demo)"}}, "none",
                 ("one_time", 299, "EUR", None), [("track-1", {"ru": "Трек 1", "en": "Track 1"}, 20, 48000, 2, 261.6)],
                 "alpha-demo-v1", state="submitted")
        make_app(db, dev1, "rejected-demo", "music", "Studio Aurora",
                 {"ru": {"title": "Отклонённое (демо)"}, "en": {"title": "Rejected (demo)"}}, "none",
                 ("one_time", 199, "EUR", None), [("t1", {"ru": "Трек", "en": "Track"}, 15, 48000, 2, 293.7)],
                 "alpha-demo-v1", state="rejected")
        make_app(db, dev1, "draft-demo", "audiobooks", "Studio Aurora",
                 {"ru": {"title": "Черновик (демо)"}, "en": {"title": "Draft (demo)"}}, "none", None,
                 [("ch-1", {"ru": "Глава 1", "en": "Chapter 1"}, 12, 48000, 1, 233.1)], "alpha-demo-v1", state="draft")
        from .adapters.devices import SIM_PROFILES
        chair = SIM_PROFILES["sim-chair-v1"]
        db.add(Device(owner_id=buyer.id, adapter="simulated", name="Simulated NAP chair", model_code="sim-chair-v1",
                      capabilities=chair.to_dict() | {"provides": sorted(chair.provides())}))
        seed_reviews(db)
    seed_multi_parcel_order()
    print("demo data created")


def seed_reviews(db) -> None:
    """Demo ratings so the catalog and app pages show the rating block (NAP-065)."""
    from .models import AppVersion, Review, ReviewReply
    data = {
        "quiet-harbor": [(5, "ru", "Спокойно и аккуратно", "Хорошо подходит для короткой паузы днём. Слой почти не слышен."),
                         (4, "ru", None, "Хотелось бы третий сеанс подлиннее."),
                         (5, "en", "Calm", "Nice two-part program, good with headphones."),
                         (4, "ru", None, None),
                         (5, "en", None, "Clean sound, no clipping.")],
        "night-stories": [(4, "en", "Nice soundscape", "Good for falling asleep, a bit short."),
                          (3, "ru", "Коротко", "Звук приятный, но 30 секунд слишком мало."),
                          (5, "en", None, None), (4, "ru", None, "Хорошая бесплатная сцена."),
                          (4, "en", None, None), (5, "ru", None, None)],
    }
    n = 0
    for slug, rows in data.items():
        app = db.scalar(select(App).where(App.slug == slug))
        if not app or db.scalar(select(Review).where(Review.app_id == app.id)):
            continue
        v = db.get(AppVersion, app.published_version_id)
        for rating, lang, title, body in rows:
            n += 1
            u = user(db, f"reviewer{n}@nap.demo", f"{'Слушатель' if lang == 'ru' else 'Listener'} {n}", ["user"],
                     locale=lang)
            db.add(Review(app_id=app.id, user_id=u.id, version_id=v.id, version_label=v.version_label,
                          rating=rating, title=title, body=body, lang=lang, status="published"))
        db.flush()
        if slug == "quiet-harbor":
            r = db.scalar(select(Review).where(Review.app_id == app.id, Review.rating == 4, Review.body.isnot(None)))
            db.add(ReviewReply(review_id=r.id, developer_id=app.developer_id,
                               body="Спасибо! Более длинный третий сеанс — в планах следующей версии."))


def seed_multi_parcel_order() -> None:
    """Paid order with two parcels (one delivered, one in transit) through the real services + mock adapters."""
    from .models import Cart, HardwareOrder, Parcel, PaymentAttempt
    from .services import billing, hardware as hw, hardware_orders as ho
    with session_scope() as db:
        buyer = db.scalar(select(User).where(User.email == "user@nap.demo"))
        cart = hw.get_cart(db, buyer.id, None)
        for code, q in (("NAP-HP1", 1), ("NAP-LINK", 1), ("NAP-CBL35", 2)):
            hw.cart_add(db, cart, db.scalar(select(Sku).where(Sku.code == code)).id, q)
        [o] = ho.create_orders_from_cart(db, buyer, cart, "ru")
        ho.set_address(db, o, "AM", {"name": "Анна Петросян", "name_latin": "Anna Petrosyan", "line1": "ул. Абовяна, 10",
                                     "line1_latin": "Abovyan st. 10", "city": "Ереван", "city_latin": "Yerevan",
                                     "postal_code": "0001", "phone": "+374 10 000000"})
        rates = ho.quote_rates(db, o)
        ho.select_rate(db, o, rates[0].id)
        oid = o.id
    with session_scope() as db:
        o = db.get(HardwareOrder, oid)
        res = ho.start_payment(db, o, db.get(User, o.buyer_id))
        ref = res["checkout_url"].rsplit("/", 1)[-1]
    with session_scope() as db:
        billing.mock_complete_checkout(db, ref, "success", db.scalar(select(User).where(User.email == "user@nap.demo")))
    billing.dispatch_outbox()
    with session_scope() as db:
        o = db.get(HardwareOrder, oid)
        seller = db.scalar(select(User).where(User.email == "seller@nap.demo"))
        ho.get_fulfillment().acknowledge(db, o, seller.id)
        its = {i.snapshot["code"]: i for i in o.items}
        p1 = ho.create_parcel(db, o, seller.id, [{"order_item_id": its["NAP-HP1"].id, "quantity": 1},
                                                 {"order_item_id": its["NAP-CBL35"].id, "quantity": 2}], 800, [300, 250, 150])
        p2 = ho.create_parcel(db, o, seller.id, [{"order_item_id": its["NAP-LINK"].id, "quantity": 1}], 400,
                              [200, 150, 100])
        ids = (p1.id, p2.id)
    for pid in ids:
        with session_scope() as db:
            o = db.get(HardwareOrder, oid)
            ho.buy_label(db, o, db.get(Parcel, pid), None)
        with session_scope() as db:
            o = db.get(HardwareOrder, oid)
            ho.handover(db, o, db.get(Parcel, pid), None)
    now = utcnow()
    with session_scope() as db:
        p1, p2 = db.get(Parcel, ids[0]), db.get(Parcel, ids[1])
    for i, st in enumerate(("in_transit", "customs", "out_for_delivery", "delivered"), 1):
        ho.simulate_tracking(p1, st, now + timedelta(seconds=i), f"seed:{p1.id}:{st}")
    ho.simulate_tracking(p2, "in_transit", now + timedelta(seconds=1), f"seed:{p2.id}:in_transit")


if __name__ == "__main__":
    run()
