# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2020 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

import operator
import re
import sre_constants as sre
import sre_parse

from hypothesis import reject, strategies as st
from hypothesis.internal.charmap import as_general_categories, categories
from hypothesis.internal.compat import int_to_byte

UNICODE_CATEGORIES = set(categories())


SPACE_CHARS = set(" \t\n\r\f\v")
UNICODE_SPACE_CHARS = SPACE_CHARS | set("\x1c\x1d\x1e\x1f\x85")
UNICODE_DIGIT_CATEGORIES = {"Nd"}
UNICODE_SPACE_CATEGORIES = set(as_general_categories("Z"))
UNICODE_LETTER_CATEGORIES = set(as_general_categories("L"))
UNICODE_WORD_CATEGORIES = set(as_general_categories(["L", "N"]))

# This is verbose, but correct on all versions of Python
BYTES_ALL = {int_to_byte(i) for i in range(256)}
BYTES_DIGIT = {b for b in BYTES_ALL if re.match(b"\\d", b)}
BYTES_SPACE = {b for b in BYTES_ALL if re.match(b"\\s", b)}
BYTES_WORD = {b for b in BYTES_ALL if re.match(b"\\w", b)}
BYTES_LOOKUP = {
    sre.CATEGORY_DIGIT: BYTES_DIGIT,
    sre.CATEGORY_SPACE: BYTES_SPACE,
    sre.CATEGORY_WORD: BYTES_WORD,
    sre.CATEGORY_NOT_DIGIT: BYTES_ALL - BYTES_DIGIT,
    sre.CATEGORY_NOT_SPACE: BYTES_ALL - BYTES_SPACE,
    sre.CATEGORY_NOT_WORD: BYTES_ALL - BYTES_WORD,
}


GROUP_CACHE_STRATEGY = st.shared(st.builds(dict), key="hypothesis.regex.group_cache")


@st.composite
def update_group(draw, group_name, strategy):
    cache = draw(GROUP_CACHE_STRATEGY)
    result = draw(strategy)
    cache[group_name] = result
    return result


@st.composite
def reuse_group(draw, group_name):
    cache = draw(GROUP_CACHE_STRATEGY)
    try:
        return cache[group_name]
    except KeyError:
        reject()


@st.composite
def group_conditional(draw, group_name, if_yes, if_no):
    cache = draw(GROUP_CACHE_STRATEGY)
    if group_name in cache:
        return draw(if_yes)
    else:
        return draw(if_no)


@st.composite
def clear_cache_after_draw(draw, base_strategy):
    cache = draw(GROUP_CACHE_STRATEGY)
    result = draw(base_strategy)
    cache.clear()
    return result


class Context:
    __slots__ = ["flags"]

    def __init__(self, flags):
        self.flags = flags


class CharactersBuilder:
    """Helper object that allows to configure `characters` strategy with
    various unicode categories and characters. Also allows negation of
    configured set.

    :param negate: If True, configure :func:`hypothesis.strategies.characters`
        to match anything other than configured character set
    :param flags: Regex flags. They affect how and which characters are matched
    """

    def __init__(self, negate=False, flags=0):
        self._categories = set()
        self._whitelist_chars = set()
        self._blacklist_chars = set()
        self._negate = negate
        self._ignorecase = flags & re.IGNORECASE
        self._unicode = not bool(flags & re.ASCII)
        self.code_to_char = chr

    @property
    def strategy(self):
        """Returns resulting strategy that generates configured char set."""
        max_codepoint = None if self._unicode else 127
        if self._negate:
            black_chars = self._blacklist_chars - self._whitelist_chars
            return st.characters(
                blacklist_categories=self._categories | {"Cc", "Cs"},
                blacklist_characters=self._whitelist_chars,
                whitelist_characters=black_chars,
                max_codepoint=max_codepoint,
            )
        white_chars = self._whitelist_chars - self._blacklist_chars
        return st.characters(
            whitelist_categories=self._categories,
            blacklist_characters=self._blacklist_chars,
            whitelist_characters=white_chars,
            max_codepoint=max_codepoint,
        )

    def add_category(self, category):
        """Update unicode state to match sre_parse object ``category``."""
        if category == sre.CATEGORY_DIGIT:
            self._categories |= UNICODE_DIGIT_CATEGORIES
        elif category == sre.CATEGORY_NOT_DIGIT:
            self._categories |= UNICODE_CATEGORIES - UNICODE_DIGIT_CATEGORIES
        elif category == sre.CATEGORY_SPACE:
            self._categories |= UNICODE_SPACE_CATEGORIES
            self._whitelist_chars |= (
                UNICODE_SPACE_CHARS if self._unicode else SPACE_CHARS
            )
        elif category == sre.CATEGORY_NOT_SPACE:
            self._categories |= UNICODE_CATEGORIES - UNICODE_SPACE_CATEGORIES
            self._blacklist_chars |= (
                UNICODE_SPACE_CHARS if self._unicode else SPACE_CHARS
            )
        elif category == sre.CATEGORY_WORD:
            self._categories |= UNICODE_WORD_CATEGORIES
            self._whitelist_chars.add("_")
        elif category == sre.CATEGORY_NOT_WORD:
            self._categories |= UNICODE_CATEGORIES - UNICODE_WORD_CATEGORIES
            self._blacklist_chars.add("_")
        else:
            raise NotImplementedError("Unknown character category: %s" % category)

    def add_char(self, char):
        """Add given char to the whitelist."""
        c = self.code_to_char(char)
        self._whitelist_chars.add(c)
        if (
            self._ignorecase
            and re.match(re.escape(c), c.swapcase(), flags=re.IGNORECASE) is not None
        ):
            self._whitelist_chars.add(c.swapcase())


class BytesBuilder(CharactersBuilder):
    def __init__(self, negate=False, flags=0):
        self._whitelist_chars = set()
        self._blacklist_chars = set()
        self._negate = negate
        self._ignorecase = flags & re.IGNORECASE
        self.code_to_char = int_to_byte

    @property
    def strategy(self):
        """Returns resulting strategy that generates configured char set."""
        allowed = self._whitelist_chars
        if self._negate:
            allowed = BYTES_ALL - allowed
        return st.sampled_from(sorted(allowed))

    def add_category(self, category):
        """Update characters state to match sre_parse object ``category``."""
        self._whitelist_chars |= BYTES_LOOKUP[category]


@st.composite
def maybe_pad(draw, regex, strategy, left_pad_strategy, right_pad_strategy):
    """Attempt to insert padding around the result of a regex draw while
    preserving the match."""
    result = draw(strategy)
    left_pad = draw(left_pad_strategy)
    if left_pad and regex.search(left_pad + result):
        result = left_pad + result
    right_pad = draw(right_pad_strategy)
    if right_pad and regex.search(result + right_pad):
        result += right_pad
    return result


def base_regex_strategy(regex, parsed=None):
    if parsed is None:
        parsed = sre_parse.parse(regex.pattern, flags=regex.flags)
    return clear_cache_after_draw(
        _strategy(parsed, Context(flags=regex.flags), isinstance(regex.pattern, str))
    )


def regex_strategy(regex, fullmatch):
    if not hasattr(regex, "pattern"):
        regex = re.compile(regex)

    is_unicode = isinstance(regex.pattern, str)

    parsed = sre_parse.parse(regex.pattern, flags=regex.flags)

    if not parsed:
        if is_unicode:
            return st.text()
        else:
            return st.binary()

    if is_unicode:
        base_padding_strategy = st.text()
        empty = st.just("")
        newline = st.just("\n")
    else:
        base_padding_strategy = st.binary()
        empty = st.just(b"")
        newline = st.just(b"\n")

    right_pad = base_padding_strategy
    left_pad = base_padding_strategy

    if fullmatch:
        right_pad = empty
    elif parsed[-1][0] == sre.AT:
        if parsed[-1][1] == sre.AT_END_STRING:
            right_pad = empty
        elif parsed[-1][1] == sre.AT_END:
            if regex.flags & re.MULTILINE:
                right_pad = st.one_of(
                    empty, st.builds(operator.add, newline, right_pad)
                )
            else:
                right_pad = st.one_of(empty, newline)
    if fullmatch:
        left_pad = empty
    elif parsed[0][0] == sre.AT:
        if parsed[0][1] == sre.AT_BEGINNING_STRING:
            left_pad = empty
        elif parsed[0][1] == sre.AT_BEGINNING:
            if regex.flags & re.MULTILINE:
                left_pad = st.one_of(empty, st.builds(operator.add, left_pad, newline))
            else:
                left_pad = empty

    base = base_regex_strategy(regex, parsed).filter(regex.search)

    return maybe_pad(regex, base, left_pad, right_pad)


def _strategy(codes, context, is_unicode):
    """Convert SRE regex parse tree to strategy that generates strings matching
    that regex represented by that parse tree.

    `codes` is either a list of SRE regex elements representations or a
    particular element representation. Each element is a tuple of element code
    (as string) and parameters. E.g. regex 'ab[0-9]+' compiles to following
    elements:

        [
            (LITERAL, 97),
            (LITERAL, 98),
            (MAX_REPEAT, (1, 4294967295, [
                (IN, [
                    (RANGE, (48, 57))
                ])
            ]))
        ]

    The function recursively traverses regex element tree and converts each
    element to strategy that generates strings that match that element.

    Context stores
    1. List of groups (for backreferences)
    2. Active regex flags (e.g. IGNORECASE, DOTALL, UNICODE, they affect
       behavior of various inner strategies)
    """

    def recurse(codes):
        return _strategy(codes, context, is_unicode)

    if is_unicode:
        empty = ""
        to_char = chr
    else:
        empty = b""
        to_char = int_to_byte
        binary_char = st.binary(min_size=1, max_size=1)

    if not isinstance(codes, tuple):
        # List of codes
        strategies = []

        i = 0
        while i < len(codes):
            if codes[i][0] == sre.LITERAL and not context.flags & re.IGNORECASE:
                # Merge subsequent "literals" into one `just()` strategy
                # that generates corresponding text if no IGNORECASE
                j = i + 1
                while j < len(codes) and codes[j][0] == sre.LITERAL:
                    j += 1

                if i + 1 < j:
                    chars = (to_char(charcode) for _, charcode in codes[i:j])
                    strategies.append(st.just(empty.join(chars)))
                    i = j
                    continue

            strategies.append(recurse(codes[i]))
            i += 1

        # We handle this separately at the top level, but some regex can
        # contain empty lists internally, so we need to handle this here too.
        if not strategies:
            return st.just(empty)

        if len(strategies) == 1:
            return strategies[0]
        return st.tuples(*strategies).map(empty.join)
    else:
        # Single code
        code, value = codes
        if code == sre.LITERAL:
            # Regex 'a' (single char)
            c = to_char(value)
            if (
                context.flags & re.IGNORECASE
                and c != c.swapcase()
                and re.match(re.escape(c), c.swapcase(), re.IGNORECASE) is not None
            ):
                # We do the explicit check for swapped-case matching because
                # eg 'ß'.upper() == 'SS' and ignorecase doesn't match it.
                return st.sampled_from([c, c.swapcase()])
            return st.just(c)

        elif code == sre.NOT_LITERAL:
            # Regex '[^a]' (negation of a single char)
            c = to_char(value)
            blacklist = {c}
            if (
                context.flags & re.IGNORECASE
                and re.match(re.escape(c), c.swapcase(), re.IGNORECASE) is not None
            ):
                # There are a few cases where .swapcase() returns two characters,
                # but is still a case-insensitive match.  In such cases we add *both*
                # characters to our blacklist, to avoid doing the wrong thing for
                # patterns such as r"[^\u0130]+" where "i\u0307" matches.
                #
                # (that's respectively 'Latin letter capital I with dot above' and
                # 'latin latter i' + 'combining dot above'; see issue #2657)
                #
                # As a final additional wrinkle, "latin letter capital I" *also*
                # case-insensitive-matches, with or without combining dot character.
                # We therefore have to chain .swapcase() calls until a fixpoint.
                stack = [c.swapcase()]
                while stack:
                    for char in stack.pop():
                        blacklist.add(char)
                        stack.extend(set(char.swapcase()) - blacklist)

            if is_unicode:
                return st.characters(blacklist_characters=blacklist)
            else:
                return binary_char.filter(lambda c: c not in blacklist)

        elif code == sre.IN:
            # Regex '[abc0-9]' (set of characters)
            negate = value[0][0] == sre.NEGATE
            if is_unicode:
                builder = CharactersBuilder(negate, context.flags)
            else:
                builder = BytesBuilder(negate, context.flags)

            for charset_code, charset_value in value:
                if charset_code == sre.NEGATE:
                    # Regex '[^...]' (negation)
                    # handled by builder = CharactersBuilder(...) above
                    pass
                elif charset_code == sre.LITERAL:
                    # Regex '[a]' (single char)
                    builder.add_char(charset_value)
                elif charset_code == sre.RANGE:
                    # Regex '[a-z]' (char range)
                    low, high = charset_value
                    for char_code in range(low, high + 1):
                        builder.add_char(char_code)
                elif charset_code == sre.CATEGORY:
                    # Regex '[\w]' (char category)
                    builder.add_category(charset_value)
                else:
                    # Currently there are no known code points other than
                    # handled here. This code is just future proofing
                    raise NotImplementedError("Unknown charset code: %s" % charset_code)
            return builder.strategy

        elif code == sre.ANY:
            # Regex '.' (any char)
            if is_unicode:
                if context.flags & re.DOTALL:
                    return st.characters()
                return st.characters(blacklist_characters="\n")
            else:
                if context.flags & re.DOTALL:
                    return binary_char
                return binary_char.filter(lambda c: c != b"\n")

        elif code == sre.AT:
            # Regexes like '^...', '...$', '\bfoo', '\Bfoo'
            # An empty string (or newline) will match the token itself, but
            # we don't and can't check the position (eg '%' at the end)
            return st.just(empty)

        elif code == sre.SUBPATTERN:
            # Various groups: '(...)', '(:...)' or '(?P<name>...)'
            old_flags = context.flags
            context.flags = (context.flags | value[1]) & ~value[2]

            strat = _strategy(value[-1], context, is_unicode)

            context.flags = old_flags

            if value[0]:
                strat = update_group(value[0], strat)

            return strat

        elif code == sre.GROUPREF:
            # Regex '\\1' or '(?P=name)' (group reference)
            return reuse_group(value)

        elif code == sre.ASSERT:
            # Regex '(?=...)' or '(?<=...)' (positive lookahead/lookbehind)
            return recurse(value[1])

        elif code == sre.ASSERT_NOT:
            # Regex '(?!...)' or '(?<!...)' (negative lookahead/lookbehind)
            return st.just(empty)

        elif code == sre.BRANCH:
            # Regex 'a|b|c' (branch)
            return st.one_of([recurse(branch) for branch in value[1]])

        elif code in [sre.MIN_REPEAT, sre.MAX_REPEAT]:
            # Regexes 'a?', 'a*', 'a+' and their non-greedy variants
            # (repeaters)
            at_least, at_most, subregex = value
            if at_most == sre.MAXREPEAT:
                at_most = None
            if at_least == 0 and at_most == 1:
                return st.just(empty) | recurse(subregex)
            return st.lists(recurse(subregex), min_size=at_least, max_size=at_most).map(
                empty.join
            )

        elif code == sre.GROUPREF_EXISTS:
            # Regex '(?(id/name)yes-pattern|no-pattern)'
            # (if group exists choice)
            return group_conditional(
                value[0],
                recurse(value[1]),
                recurse(value[2]) if value[2] else st.just(empty),
            )

        else:
            # Currently there are no known code points other than handled here.
            # This code is just future proofing
            raise NotImplementedError("Unknown code point: %s" % repr(code))
