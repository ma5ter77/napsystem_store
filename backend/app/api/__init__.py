from ..config import get_settings
from . import about, admin, audio, auth, catalog, commerce, developer, devices, hardware, licenses, manage, news, reviews, seller, testing

routers = [about.router, auth.router, catalog.router, developer.router, commerce.router, hardware.router, hardware.account_router, seller.router,
           devices.router, admin.router, reviews.router, manage.router, audio.router, news.router,
           licenses.router]
if get_settings().env != "production":
    routers.append(testing.router)
