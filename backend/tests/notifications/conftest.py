from tests.closing.conftest import ended  # noqa: F401  shared fixtures
from tests.lifecycle.conftest import (  # noqa: F401  shared fixtures
    admin_service,
    api,
    auction_service,
    auth_service,
    bid_service,
    catalog_service,
    category,
    clock,
    close_queue,
    email,
    limiter,
    make_user,
    storage,
    user_service,
)
from tests.notifications.support import (  # noqa: F401  fixtures
    notification_service,
    outbox,
    quiet_email,
)
