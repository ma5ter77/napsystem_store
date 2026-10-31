from ..config import get_settings
from . import about, admin, auth, catalog, commerce, developer, devices, hardware, reviews, seller, testing

routers = [about.router, auth.router, catalog.router, developer.router, commerce.router, hardware.router, seller.router,
           devices.router, admin.router, reviews.router]
if get_settings().env != "production":
    routers.append(testing.router)
