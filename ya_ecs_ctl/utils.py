import itertools
import pprint
from colored import attr
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.validation import Validator, ValidationError
from terminaltables import AsciiTable

class ChoicesValidator(Validator):
    def __init__(self, choices):
        self.choices = choices

    def validate(self, document):
        text = document.text

        if text not in self.choices:
            raise ValidationError(message='Use Arrow down key to select from the list', cursor_position=0)


class ChoicesCompleter(Completer):
    def __init__(self, choices):
        self.choices = choices

    def get_completions(self, document, complete_event):
        for c in self.choices:
            if c.lower().startswith(document.text.lower()):
                yield Completion(c, start_position=-len(document.text))

def lowerCaseFirstLetter(str):
    return str[0].lower() + str[1:]

def change_keys(obj, convert):
    """
    Recursively goes through the dictionary obj and replaces keys with the convert function.
    """
    if isinstance(obj, (str, int, float)):
        return obj
    if isinstance(obj, dict):
        new = obj.__class__()
        for k, v in obj.items():
            new[convert(k)] = change_keys(v, convert)
    elif isinstance(obj, (list, set, tuple)):
        new = obj.__class__(change_keys(v, convert) for v in obj)
    else:
        return obj
    return new


def chunks(iterable,size):
    it = iter(iterable)
    chunk = tuple(itertools.islice(it,size))
    while chunk:
        yield chunk
        chunk = tuple(itertools.islice(it,size))

def print_table(header, data):
    print("")
    print(AsciiTable([header] + data).table)
    print("")


def dump(data):
    pprint.pprint(data)


# ugly helper for colored output
reset = attr('reset')