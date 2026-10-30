from ..config import get_settings
from . import admin, auth, catalog, commerce, developer, devices, hardware, seller, testing

routers = [auth.router, catalog.router, developer.router, commerce.router, hardware.router, seller.router,
           devices.router, admin.router]
if get_settings().env != "production":
    routers.append(testing.router)
