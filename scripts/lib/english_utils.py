#!/usr/bin/env python3
"""Shared English text utilities — common words, non-dialogue markers.

Used by: build_glossary.py, noun_checker.py, auto_classify.py, oped_fixer.py
"""

import re

# Common English function words that are NEVER proper nouns
COMMON_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall',
    'should', 'may', 'might', 'must', 'can', 'could', 'shall',
    'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her',
    'us', 'them', 'my', 'your', 'his', 'its', 'our', 'their',
    'this', 'that', 'these', 'those', 'here', 'there', 'where', 'when',
    'what', 'who', 'whom', 'which', 'why', 'how',
    'not', 'no', 'yes', 'so', 'if', 'then', 'than', 'too', 'very',
    'just', 'now', 'also', 'only', 'even', 'still', 'much', 'such',
    'all', 'some', 'any', 'each', 'every', 'both', 'few', 'more', 'most',
    'one', 'two', 'three', 'first', 'second', 'last',
    'and', 'but', 'or', 'for', 'nor', 'yet', 'with', 'without',
    'in', 'on', 'at', 'to', 'from', 'by', 'of', 'as', 'into', 'onto',
    'up', 'down', 'out', 'off', 'over', 'under', 'about', 'between',
    'through', 'during', 'before', 'after', 'above', 'below',
    'well', 'ok', 'okay', 'oh', 'ah', 'hey', 'hi', 'hello', 'goodbye',
    'please', 'thanks', 'thank', 'sorry', 'really', 'right', 'yeah',
    'gonna', 'wanna', 'gotta', 'kinda', 'sorta',
    'let', 'get', 'got', 'go', 'come', 'came', 'went', 'make', 'made',
    'take', 'took', 'see', 'saw', 'know', 'knew', 'think', 'thought',
    'say', 'said', 'tell', 'told', 'want', 'need', 'like', 'love',
    'back', 'away', 'again', 'already', 'yet', 'ever', 'never',
    'always', 'sometimes', 'often', 'usually',
    'mr', 'mrs', 'ms', 'miss', 'dr', 'prof', 'sir', 'lord', 'lady',
    'captain', 'commander', 'general', 'president', 'king', 'queen',
    'prince', 'princess', 'duke', 'count',
})

# English exclamation/filler words — not meaningful dialogue
EXCLAMATION_WORDS = frozenset({
    'um', 'uh', 'oh', 'ah', 'eh', 'hmm', 'er', 'hmph', 'ugh', 'ack',
    'ow', 'whoa', 'hey', 'huh', 'mm', 'hm', 'ha', 'heh', 'meh',
    'shh', 'psst', 'ughh', 'argh', 'grr', 'ahem', 'ooh', 'aah',
    'wah', 'yay', 'woohoo', 'boo', 'haha', 'hehe', 'hoho',
})

# Non-word patterns: dashes, repeated chars, breathing/filler sounds
NON_WORD_RE = re.compile(
    r'^[-—]{2,}$|'
    r'^(.)\1{2,}$|'
    r'^(um|uh|oh|ah|eh|hmm|er|hmph|ugh|ack)+$'
)

# Honorific / title suffix patterns
_HONORIFIC_LIST = (
    'mr', 'mrs', 'ms', 'miss', 'dr', 'prof', 'sir', 'lord',
    'captain', 'commander', 'general', 'president', 'prince',
    'princess', 'duke', 'count', 'senpai', 'sensei', 'sama',
    'chan', 'kun', 'san',
)

# Non-dialogue editorial markers — sound effects, music cues, audience reactions
# These should ALWAYS be deleted regardless of speech overlap.
NON_DIALOGUE_PATTERNS = [
    r'^\[Music\]$',
    r'^\[BGM\]$',
    r'^\[Applause\]$',
    r'^\[Laughter\]$',
    r'^\[Cheers\]$',
    r'^\[Scream\]$',
    r'^\[SFX\]$',
    r'^\[Sound Effect\]$',
    r'^\[Footsteps\]$',
    r'^\[Bell\]$',
    r'^\[Whistle\]$',
    r'^\[Thunder\]$',
    r'^\[Wind\]$',
    r'^\[Waves\]$',
    r'^\[Rain\]$',
    r'^\[Explosion\]$',
    r'^\[Gunshot\]$',
    r'^\[Car\]$',
    r'^\[Airplane\]$',
    r'^\[Telephone\]$',
    r'^\[Knock\]$',
    r'^\[Door\]$',
    r'^\[SE\]$',
    r'^\[Murmur\]$',
    r'^\[Stir\]$',
]

# English proper noun patterns
PROPER_NOUN_PATTERNS = [
    r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',   # Dr. Elefun, Astro Boy
    r'\b[A-Z][a-z]{2,}\b',              # Astro, Uran, Atlas
]
