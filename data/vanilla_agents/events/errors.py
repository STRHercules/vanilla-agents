from ..contracts import VanillaError


class EventSchemaError(VanillaError):
    code = "event_schema"


class FeedError(VanillaError):
    code = "feed_error"


class TemplateError(VanillaError):
    code = "eli5_template"
