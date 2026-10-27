"""Route groups are assembled explicitly so ownership remains easy to find."""

from . import conversations, diagnostics, library, personal, personal_preferences, system, workspace

ROUTE_GROUPS = (system, conversations, workspace, personal, personal_preferences, library, diagnostics)


def mount_routes(app, context):
    for group in ROUTE_GROUPS:
        app.include_router(group.build_router(context))
