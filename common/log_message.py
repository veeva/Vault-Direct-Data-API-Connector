import datetime
import traceback


def log_message(log_level, message, exception=None, context=None):
    """
    Logs a message with the specified log level.

    :param log_level: The severity level of the log message.
    :param message: The log message to be logged.
    :param exception: An exception object to log the exception details and traceback. Defaults to None.
    :param context: Additional contextual information. Defaults to None.
    """

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{log_level}] {timestamp} - {message}"
    if exception:
        log_entry += f"\nException: {exception}\n{traceback.format_exc()}\n{traceback.print_exc()}"
    if context:
        log_entry += f"\nContext: {context}"
    print(log_entry)
