from ..config import get_settings
from . import about, admin, audio, auth, catalog, commerce, developer, devices, hardware, manage, reviews, seller, testing

routers = [about.router, auth.router, catalog.router, developer.router, commerce.router, hardware.router, hardware.account_router, seller.router,
           devices.router, admin.router, reviews.router, manage.router, audio.router]
if get_settings().env != "production":
    routers.append(testing.router)
