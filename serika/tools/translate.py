"""Offline phrasebook translation — no API, no network, no model download.

Real neural machine translation needs either a hosted API (which the operator
declined) or a multi-megabyte on-device model. Neither fits a dependency-free
Python server that promises to make no external requests. So this is honest
about what it is: a curated **phrasebook and word dictionary** across eight
languages, doing exact phrase lookup first and a word-by-word gloss as a
fallback. It nails the things people actually type at a search box — "thank you
in japanese", "how much in french", "where is the bathroom in spanish" — and it
says plainly when it has only managed a literal, word-by-word rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Translation", "parse_translate", "LANGUAGES"]

# code -> (English name, endonym)
LANGUAGES = {
    "es": ("Spanish", "Español"),
    "fr": ("French", "Français"),
    "de": ("German", "Deutsch"),
    "it": ("Italian", "Italiano"),
    "pt": ("Portuguese", "Português"),
    "nl": ("Dutch", "Nederlands"),
    "ja": ("Japanese", "日本語"),
    "zh": ("Chinese", "中文"),
}

_LANG_ALIASES = {
    "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
    "french": "fr", "français": "fr", "francais": "fr",
    "german": "de", "deutsch": "de",
    "italian": "it", "italiano": "it",
    "portuguese": "pt", "português": "pt", "portugues": "pt",
    "dutch": "nl", "nederlands": "nl", "flemish": "nl",
    "japanese": "ja", "日本語": "ja", "nihongo": "ja",
    "chinese": "zh", "mandarin": "zh", "中文": "zh",
}

# Whole phrases, keyed by a normalised English form. Order of language columns:
# es, fr, de, it, pt, nl, ja, zh.
_LANGS = ("es", "fr", "de", "it", "pt", "nl", "ja", "zh")
_PHRASES: dict[str, tuple[str, ...]] = {
    "hello": ("Hola", "Bonjour", "Hallo", "Ciao", "Olá", "Hallo",
              "こんにちは (konnichiwa)", "你好 (nǐ hǎo)"),
    "goodbye": ("Adiós", "Au revoir", "Auf Wiedersehen", "Arrivederci",
                "Adeus", "Tot ziens", "さようなら (sayōnara)", "再见 (zàijiàn)"),
    "good morning": ("Buenos días", "Bonjour", "Guten Morgen", "Buongiorno",
                     "Bom dia", "Goedemorgen", "おはよう (ohayō)",
                     "早上好 (zǎoshang hǎo)"),
    "good night": ("Buenas noches", "Bonne nuit", "Gute Nacht", "Buonanotte",
                   "Boa noite", "Goedenacht", "おやすみ (oyasumi)",
                   "晚安 (wǎn'ān)"),
    "thank you": ("Gracias", "Merci", "Danke", "Grazie", "Obrigado",
                  "Dank je", "ありがとう (arigatō)", "谢谢 (xièxie)"),
    "thanks": ("Gracias", "Merci", "Danke", "Grazie", "Obrigado", "Bedankt",
               "ありがとう (arigatō)", "谢谢 (xièxie)"),
    "please": ("Por favor", "S'il vous plaît", "Bitte", "Per favore",
               "Por favor", "Alsjeblieft", "お願いします (onegaishimasu)",
               "请 (qǐng)"),
    "you're welcome": ("De nada", "De rien", "Bitte schön", "Prego",
                       "De nada", "Graag gedaan", "どういたしまして (dōitashimashite)",
                       "不客气 (bú kèqi)"),
    "yes": ("Sí", "Oui", "Ja", "Sì", "Sim", "Ja", "はい (hai)", "是 (shì)"),
    "no": ("No", "Non", "Nein", "No", "Não", "Nee", "いいえ (iie)", "不 (bù)"),
    "excuse me": ("Perdón", "Excusez-moi", "Entschuldigung", "Scusi",
                  "Com licença", "Pardon", "すみません (sumimasen)",
                  "对不起 (duìbuqǐ)"),
    "sorry": ("Lo siento", "Désolé", "Es tut mir leid", "Mi dispiace",
              "Desculpe", "Sorry", "ごめんなさい (gomen nasai)",
              "对不起 (duìbuqǐ)"),
    "how are you": ("¿Cómo estás?", "Comment ça va ?", "Wie geht's?",
                    "Come stai?", "Como está?", "Hoe gaat het?",
                    "元気ですか (genki desu ka)", "你好吗 (nǐ hǎo ma)"),
    "i love you": ("Te quiero", "Je t'aime", "Ich liebe dich", "Ti amo",
                   "Amo-te", "Ik hou van jou", "愛してる (aishiteru)",
                   "我爱你 (wǒ ài nǐ)"),
    "i don't understand": ("No entiendo", "Je ne comprends pas",
                           "Ich verstehe nicht", "Non capisco", "Não entendo",
                           "Ik begrijp het niet", "わかりません (wakarimasen)",
                           "我不懂 (wǒ bù dǒng)"),
    "do you speak english": ("¿Hablas inglés?", "Parlez-vous anglais ?",
                             "Sprechen Sie Englisch?", "Parli inglese?",
                             "Você fala inglês?", "Spreek je Engels?",
                             "英語を話せますか (eigo o hanasemasu ka)",
                             "你会说英语吗 (nǐ huì shuō yīngyǔ ma)"),
    "where is the bathroom": ("¿Dónde está el baño?", "Où sont les toilettes ?",
                              "Wo ist die Toilette?", "Dov'è il bagno?",
                              "Onde é o banheiro?", "Waar is het toilet?",
                              "トイレはどこですか (toire wa doko desu ka)",
                              "洗手间在哪里 (xǐshǒujiān zài nǎlǐ)"),
    "how much is it": ("¿Cuánto cuesta?", "Combien ça coûte ?",
                       "Wie viel kostet das?", "Quanto costa?", "Quanto custa?",
                       "Hoeveel kost het?", "いくらですか (ikura desu ka)",
                       "多少钱 (duōshao qián)"),
    "how much": ("¿Cuánto?", "Combien ?", "Wie viel?", "Quanto?", "Quanto?",
                 "Hoeveel?", "いくら (ikura)", "多少 (duōshao)"),
    "cheers": ("Salud", "Santé", "Prost", "Salute", "Saúde", "Proost",
               "乾杯 (kanpai)", "干杯 (gānbēi)"),
    "help": ("Ayuda", "À l'aide", "Hilfe", "Aiuto", "Socorro", "Help",
             "助けて (tasukete)", "救命 (jiùmìng)"),
    "welcome": ("Bienvenido", "Bienvenue", "Willkommen", "Benvenuto",
                "Bem-vindo", "Welkom", "ようこそ (yōkoso)", "欢迎 (huānyíng)"),
    "my name is": ("Me llamo", "Je m'appelle", "Ich heiße", "Mi chiamo",
                   "Meu nome é", "Mijn naam is", "私の名前は (watashi no namae wa)",
                   "我叫 (wǒ jiào)"),
    "i would like": ("Quisiera", "Je voudrais", "Ich möchte", "Vorrei",
                     "Eu gostaria", "Ik wil graag", "〜がほしい (ga hoshii)",
                     "我想要 (wǒ xiǎng yào)"),
    "the bill please": ("La cuenta, por favor", "L'addition, s'il vous plaît",
                        "Die Rechnung, bitte", "Il conto, per favore",
                        "A conta, por favor", "De rekening, alsjeblieft",
                        "お会計お願いします (okaikei onegaishimasu)",
                        "买单 (mǎidān)"),
}

# Single-word dictionary for the word-by-word fallback.
_WORDS: dict[str, tuple[str, ...]] = {
    "water": ("agua", "eau", "Wasser", "acqua", "água", "water",
              "水 (mizu)", "水 (shuǐ)"),
    "food": ("comida", "nourriture", "Essen", "cibo", "comida", "eten",
             "食べ物 (tabemono)", "食物 (shíwù)"),
    "coffee": ("café", "café", "Kaffee", "caffè", "café", "koffie",
               "コーヒー (kōhī)", "咖啡 (kāfēi)"),
    "beer": ("cerveza", "bière", "Bier", "birra", "cerveja", "bier",
             "ビール (bīru)", "啤酒 (píjiǔ)"),
    "one": ("uno", "un", "eins", "uno", "um", "een", "一 (ichi)", "一 (yī)"),
    "two": ("dos", "deux", "zwei", "due", "dois", "twee", "二 (ni)", "二 (èr)"),
    "three": ("tres", "trois", "drei", "tre", "três", "drie", "三 (san)",
              "三 (sān)"),
    "cat": ("gato", "chat", "Katze", "gatto", "gato", "kat", "猫 (neko)",
            "猫 (māo)"),
    "dog": ("perro", "chien", "Hund", "cane", "cachorro", "hond", "犬 (inu)",
            "狗 (gǒu)"),
    "friend": ("amigo", "ami", "Freund", "amico", "amigo", "vriend",
               "友達 (tomodachi)", "朋友 (péngyǒu)"),
    "love": ("amor", "amour", "Liebe", "amore", "amor", "liefde",
             "愛 (ai)", "爱 (ài)"),
    "good": ("bueno", "bon", "gut", "buono", "bom", "goed", "良い (yoi)",
             "好 (hǎo)"),
    "big": ("grande", "grand", "groß", "grande", "grande", "groot",
            "大きい (ōkii)", "大 (dà)"),
    "small": ("pequeño", "petit", "klein", "piccolo", "pequeno", "klein",
              "小さい (chiisai)", "小 (xiǎo)"),
    "today": ("hoy", "aujourd'hui", "heute", "oggi", "hoje", "vandaag",
              "今日 (kyō)", "今天 (jīntiān)"),
    "tomorrow": ("mañana", "demain", "morgen", "domani", "amanhã", "morgen",
                 "明日 (ashita)", "明天 (míngtiān)"),
}

_COL = {code: i for i, code in enumerate(_LANGS)}


@dataclass
class Translation:
    source: str
    target_code: str
    target_name: str
    target_endonym: str
    result: str
    literal: bool          # True when only a word-by-word gloss was possible
    per_word: list[tuple[str, str]]


_TRANSLATE_RE = re.compile(
    r"^\s*(?:translate\s+)?(.+?)\s+(?:in|to|into|en|auf)\s+"
    r"(spanish|español|espanol|castellano|french|français|francais|german|"
    r"deutsch|italian|italiano|portuguese|português|portugues|dutch|"
    r"nederlands|flemish|japanese|日本語|nihongo|chinese|mandarin|中文)\s*\??$",
    re.I,
)
_HOW_SAY_RE = re.compile(
    r"^\s*how\s+(?:do\s+you|to)\s+say\s+(.+?)\s+in\s+"
    r"(spanish|french|german|italian|portuguese|dutch|japanese|chinese|"
    r"mandarin)\s*\??$",
    re.I,
)


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s']", "", text.strip().lower()).strip()


def _resolve_lang(name: str) -> str | None:
    return _LANG_ALIASES.get(name.strip().lower())


def parse_translate(query: str) -> Translation | None:
    """``thank you in japanese``, ``translate good morning to french``,
    ``how do you say water in italian``."""
    text = query.strip()
    if len(text) > 200:
        return None

    # "how do you say X in Y" first — otherwise the generic "X in Y" pattern
    # greedily swallows the "how do you say" preamble into the phrase.
    match = _HOW_SAY_RE.match(text) or _TRANSLATE_RE.match(text)
    if not match:
        return None
    phrase_raw, lang_raw = match.group(1), match.group(2)
    code = _resolve_lang(lang_raw)
    if not code:
        return None
    column = _COL[code]
    name, endonym = LANGUAGES[code]

    key = _normalise(phrase_raw)
    if not key:
        return None

    # 1) Exact phrase.
    if key in _PHRASES:
        return Translation(
            source=phrase_raw.strip(), target_code=code, target_name=name,
            target_endonym=endonym, result=_PHRASES[key][column],
            literal=False, per_word=[],
        )

    # 2) Word-by-word gloss. Only worth returning if we actually know some of
    #    the words — otherwise fall through to normal search results.
    words = key.split()
    if len(words) > 8:
        return None
    per_word: list[tuple[str, str]] = []
    known = 0
    for word in words:
        if word in _PHRASES:
            per_word.append((word, _PHRASES[word][column]))
            known += 1
        elif word in _WORDS:
            per_word.append((word, _WORDS[word][column]))
            known += 1
        else:
            per_word.append((word, word))
    if known == 0 or known < len(words) / 2:
        return None

    glossed = " ".join(t for _, t in per_word)
    return Translation(
        source=phrase_raw.strip(), target_code=code, target_name=name,
        target_endonym=endonym, result=glossed, literal=True,
        per_word=per_word,
    )
