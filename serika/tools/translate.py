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

# A bundled bilingual dictionary of common English words, one row per word with
# its equivalents in the order (es, fr, de, it, pt, nl, ja, zh). It is large
# enough to gloss everyday sentences word-by-word; it is not a grammar engine,
# so the result is literal, not fluent — the UI says so.
_WORDS: dict[str, tuple[str, ...]] = {
    # pronouns
    "i": ("yo", "je", "ich", "io", "eu", "ik", "私 (watashi)", "我 (wǒ)"),
    "you": ("tú", "tu", "du", "tu", "você", "jij", "あなた (anata)", "你 (nǐ)"),
    "he": ("él", "il", "er", "lui", "ele", "hij", "彼 (kare)", "他 (tā)"),
    "she": ("ella", "elle", "sie", "lei", "ela", "zij", "彼女 (kanojo)", "她 (tā)"),
    "we": ("nosotros", "nous", "wir", "noi", "nós", "wij", "私たち (watashitachi)", "我们 (wǒmen)"),
    "they": ("ellos", "ils", "sie", "loro", "eles", "zij", "彼ら (karera)", "他们 (tāmen)"),
    "it": ("eso", "ça", "es", "esso", "isso", "het", "それ (sore)", "它 (tā)"),
    "me": ("mí", "moi", "mich", "me", "me", "mij", "私 (watashi)", "我 (wǒ)"),
    "my": ("mi", "mon", "mein", "mio", "meu", "mijn", "私の (watashi no)", "我的 (wǒ de)"),
    "your": ("tu", "ton", "dein", "tuo", "seu", "jouw", "あなたの (anata no)", "你的 (nǐ de)"),
    "this": ("este", "ce", "dieser", "questo", "este", "dit", "これ (kore)", "这 (zhè)"),
    "that": ("ese", "ce", "das", "quello", "aquele", "dat", "あれ (are)", "那 (nà)"),
    # question words
    "what": ("qué", "quoi", "was", "cosa", "o que", "wat", "何 (nani)", "什么 (shénme)"),
    "who": ("quién", "qui", "wer", "chi", "quem", "wie", "誰 (dare)", "谁 (shéi)"),
    "where": ("dónde", "où", "wo", "dove", "onde", "waar", "どこ (doko)", "哪里 (nǎlǐ)"),
    "when": ("cuándo", "quand", "wann", "quando", "quando", "wanneer", "いつ (itsu)", "什么时候 (shénme shíhou)"),
    "why": ("por qué", "pourquoi", "warum", "perché", "por que", "waarom", "なぜ (naze)", "为什么 (wèishénme)"),
    "how": ("cómo", "comment", "wie", "come", "como", "hoe", "どう (dō)", "怎么 (zěnme)"),
    "which": ("cuál", "quel", "welcher", "quale", "qual", "welke", "どれ (dore)", "哪个 (nǎge)"),
    # common verbs
    "is": ("es", "est", "ist", "è", "é", "is", "です (desu)", "是 (shì)"),
    "are": ("son", "sont", "sind", "sono", "são", "zijn", "です (desu)", "是 (shì)"),
    "am": ("soy", "suis", "bin", "sono", "sou", "ben", "です (desu)", "是 (shì)"),
    "was": ("era", "était", "war", "era", "era", "was", "でした (deshita)", "是 (shì)"),
    "be": ("ser", "être", "sein", "essere", "ser", "zijn", "ある (aru)", "是 (shì)"),
    "have": ("tener", "avoir", "haben", "avere", "ter", "hebben", "持つ (motsu)", "有 (yǒu)"),
    "has": ("tiene", "a", "hat", "ha", "tem", "heeft", "持つ (motsu)", "有 (yǒu)"),
    "do": ("hacer", "faire", "tun", "fare", "fazer", "doen", "する (suru)", "做 (zuò)"),
    "go": ("ir", "aller", "gehen", "andare", "ir", "gaan", "行く (iku)", "去 (qù)"),
    "come": ("venir", "venir", "kommen", "venire", "vir", "komen", "来る (kuru)", "来 (lái)"),
    "want": ("querer", "vouloir", "wollen", "volere", "querer", "willen", "欲しい (hoshii)", "要 (yào)"),
    "need": ("necesitar", "avoir besoin", "brauchen", "aver bisogno", "precisar", "nodig hebben", "必要 (hitsuyō)", "需要 (xūyào)"),
    "like": ("gustar", "aimer", "mögen", "piacere", "gostar", "houden van", "好き (suki)", "喜欢 (xǐhuan)"),
    "know": ("saber", "savoir", "wissen", "sapere", "saber", "weten", "知る (shiru)", "知道 (zhīdào)"),
    "see": ("ver", "voir", "sehen", "vedere", "ver", "zien", "見る (miru)", "看 (kàn)"),
    "eat": ("comer", "manger", "essen", "mangiare", "comer", "eten", "食べる (taberu)", "吃 (chī)"),
    "drink": ("beber", "boire", "trinken", "bere", "beber", "drinken", "飲む (nomu)", "喝 (hē)"),
    "speak": ("hablar", "parler", "sprechen", "parlare", "falar", "spreken", "話す (hanasu)", "说 (shuō)"),
    "buy": ("comprar", "acheter", "kaufen", "comprare", "comprar", "kopen", "買う (kau)", "买 (mǎi)"),
    "can": ("poder", "pouvoir", "können", "potere", "poder", "kunnen", "できる (dekiru)", "能 (néng)"),
    "make": ("hacer", "faire", "machen", "fare", "fazer", "maken", "作る (tsukuru)", "做 (zuò)"),
    "find": ("encontrar", "trouver", "finden", "trovare", "encontrar", "vinden", "見つける (mitsukeru)", "找 (zhǎo)"),
    "help": ("ayudar", "aider", "helfen", "aiutare", "ajudar", "helpen", "助ける (tasukeru)", "帮助 (bāngzhù)"),
    "give": ("dar", "donner", "geben", "dare", "dar", "geven", "あげる (ageru)", "给 (gěi)"),
    "take": ("tomar", "prendre", "nehmen", "prendere", "tomar", "nemen", "取る (toru)", "拿 (ná)"),
    "love": ("amar", "aimer", "lieben", "amare", "amar", "houden van", "愛する (aisuru)", "爱 (ài)"),
    # articles / conjunctions / prepositions
    "the": ("el", "le", "der", "il", "o", "de", "", "这"),
    "a": ("un", "un", "ein", "un", "um", "een", "", "一"),
    "and": ("y", "et", "und", "e", "e", "en", "と (to)", "和 (hé)"),
    "or": ("o", "ou", "oder", "o", "ou", "of", "または (mataha)", "或 (huò)"),
    "but": ("pero", "mais", "aber", "ma", "mas", "maar", "でも (demo)", "但是 (dànshì)"),
    "not": ("no", "ne pas", "nicht", "non", "não", "niet", "ない (nai)", "不 (bù)"),
    "with": ("con", "avec", "mit", "con", "com", "met", "と (to)", "和 (hé)"),
    "without": ("sin", "sans", "ohne", "senza", "sem", "zonder", "なしで (nashi de)", "没有 (méiyǒu)"),
    "for": ("para", "pour", "für", "per", "para", "voor", "のために (no tame ni)", "为 (wèi)"),
    "to": ("a", "à", "zu", "a", "para", "naar", "へ (e)", "到 (dào)"),
    "from": ("de", "de", "von", "da", "de", "van", "から (kara)", "从 (cóng)"),
    "in": ("en", "dans", "in", "in", "em", "in", "に (ni)", "在 (zài)"),
    "on": ("en", "sur", "auf", "su", "em", "op", "の上 (no ue)", "在...上 (zài...shàng)"),
    "at": ("en", "à", "an", "a", "em", "bij", "で (de)", "在 (zài)"),
    "of": ("de", "de", "von", "di", "de", "van", "の (no)", "的 (de)"),
    "very": ("muy", "très", "sehr", "molto", "muito", "zeer", "とても (totemo)", "很 (hěn)"),
    "here": ("aquí", "ici", "hier", "qui", "aqui", "hier", "ここ (koko)", "这里 (zhèlǐ)"),
    "there": ("allí", "là", "dort", "lì", "lá", "daar", "そこ (soko)", "那里 (nàlǐ)"),
    "now": ("ahora", "maintenant", "jetzt", "ora", "agora", "nu", "今 (ima)", "现在 (xiànzài)"),
    "please": ("por favor", "s'il vous plaît", "bitte", "per favore", "por favor", "alsjeblieft", "お願い (onegai)", "请 (qǐng)"),
    "yes": ("sí", "oui", "ja", "sì", "sim", "ja", "はい (hai)", "是 (shì)"),
    "no": ("no", "non", "nein", "no", "não", "nee", "いいえ (iie)", "不 (bù)"),
    # people / places
    "friend": ("amigo", "ami", "Freund", "amico", "amigo", "vriend", "友達 (tomodachi)", "朋友 (péngyǒu)"),
    "man": ("hombre", "homme", "Mann", "uomo", "homem", "man", "男 (otoko)", "男人 (nánrén)"),
    "woman": ("mujer", "femme", "Frau", "donna", "mulher", "vrouw", "女 (onna)", "女人 (nǚrén)"),
    "child": ("niño", "enfant", "Kind", "bambino", "criança", "kind", "子供 (kodomo)", "孩子 (háizi)"),
    "family": ("familia", "famille", "Familie", "famiglia", "família", "familie", "家族 (kazoku)", "家庭 (jiātíng)"),
    "house": ("casa", "maison", "Haus", "casa", "casa", "huis", "家 (ie)", "房子 (fángzi)"),
    "home": ("casa", "maison", "Zuhause", "casa", "casa", "thuis", "家 (ie)", "家 (jiā)"),
    "city": ("ciudad", "ville", "Stadt", "città", "cidade", "stad", "都市 (toshi)", "城市 (chéngshì)"),
    "country": ("país", "pays", "Land", "paese", "país", "land", "国 (kuni)", "国家 (guójiā)"),
    "street": ("calle", "rue", "Straße", "strada", "rua", "straat", "通り (tōri)", "街 (jiē)"),
    "hotel": ("hotel", "hôtel", "Hotel", "albergo", "hotel", "hotel", "ホテル (hoteru)", "酒店 (jiǔdiàn)"),
    "airport": ("aeropuerto", "aéroport", "Flughafen", "aeroporto", "aeroporto", "luchthaven", "空港 (kūkō)", "机场 (jīchǎng)"),
    "station": ("estación", "gare", "Bahnhof", "stazione", "estação", "station", "駅 (eki)", "车站 (chēzhàn)"),
    "bathroom": ("baño", "toilettes", "Toilette", "bagno", "banheiro", "toilet", "トイレ (toire)", "洗手间 (xǐshǒujiān)"),
    "restaurant": ("restaurante", "restaurant", "Restaurant", "ristorante", "restaurante", "restaurant", "レストラン (resutoran)", "餐厅 (cāntīng)"),
    # things
    "water": ("agua", "eau", "Wasser", "acqua", "água", "water", "水 (mizu)", "水 (shuǐ)"),
    "food": ("comida", "nourriture", "Essen", "cibo", "comida", "eten", "食べ物 (tabemono)", "食物 (shíwù)"),
    "coffee": ("café", "café", "Kaffee", "caffè", "café", "koffie", "コーヒー (kōhī)", "咖啡 (kāfēi)"),
    "tea": ("té", "thé", "Tee", "tè", "chá", "thee", "お茶 (ocha)", "茶 (chá)"),
    "beer": ("cerveza", "bière", "Bier", "birra", "cerveja", "bier", "ビール (bīru)", "啤酒 (píjiǔ)"),
    "wine": ("vino", "vin", "Wein", "vino", "vinho", "wijn", "ワイン (wain)", "葡萄酒 (pútáojiǔ)"),
    "bread": ("pan", "pain", "Brot", "pane", "pão", "brood", "パン (pan)", "面包 (miànbāo)"),
    "money": ("dinero", "argent", "Geld", "denaro", "dinheiro", "geld", "お金 (okane)", "钱 (qián)"),
    "time": ("tiempo", "temps", "Zeit", "tempo", "tempo", "tijd", "時間 (jikan)", "时间 (shíjiān)"),
    "day": ("día", "jour", "Tag", "giorno", "dia", "dag", "日 (hi)", "天 (tiān)"),
    "car": ("coche", "voiture", "Auto", "auto", "carro", "auto", "車 (kuruma)", "车 (chē)"),
    "book": ("libro", "livre", "Buch", "libro", "livro", "boek", "本 (hon)", "书 (shū)"),
    "phone": ("teléfono", "téléphone", "Telefon", "telefono", "telefone", "telefoon", "電話 (denwa)", "电话 (diànhuà)"),
    "world": ("mundo", "monde", "Welt", "mondo", "mundo", "wereld", "世界 (sekai)", "世界 (shìjiè)"),
    "name": ("nombre", "nom", "Name", "nome", "nome", "naam", "名前 (namae)", "名字 (míngzi)"),
    "cat": ("gato", "chat", "Katze", "gatto", "gato", "kat", "猫 (neko)", "猫 (māo)"),
    "dog": ("perro", "chien", "Hund", "cane", "cachorro", "hond", "犬 (inu)", "狗 (gǒu)"),
    # adjectives
    "good": ("bueno", "bon", "gut", "buono", "bom", "goed", "良い (yoi)", "好 (hǎo)"),
    "bad": ("malo", "mauvais", "schlecht", "cattivo", "mau", "slecht", "悪い (warui)", "坏 (huài)"),
    "big": ("grande", "grand", "groß", "grande", "grande", "groot", "大きい (ōkii)", "大 (dà)"),
    "small": ("pequeño", "petit", "klein", "piccolo", "pequeno", "klein", "小さい (chiisai)", "小 (xiǎo)"),
    "hot": ("caliente", "chaud", "heiß", "caldo", "quente", "heet", "熱い (atsui)", "热 (rè)"),
    "cold": ("frío", "froid", "kalt", "freddo", "frio", "koud", "冷たい (tsumetai)", "冷 (lěng)"),
    "new": ("nuevo", "nouveau", "neu", "nuovo", "novo", "nieuw", "新しい (atarashii)", "新 (xīn)"),
    "old": ("viejo", "vieux", "alt", "vecchio", "velho", "oud", "古い (furui)", "老 (lǎo)"),
    "beautiful": ("hermoso", "beau", "schön", "bello", "bonito", "mooi", "美しい (utsukushii)", "美丽 (měilì)"),
    "happy": ("feliz", "heureux", "glücklich", "felice", "feliz", "blij", "幸せ (shiawase)", "快乐 (kuàilè)"),
    "great": ("genial", "génial", "großartig", "fantastico", "ótimo", "geweldig", "素晴らしい (subarashii)", "很棒 (hěn bàng)"),
    "much": ("mucho", "beaucoup", "viel", "molto", "muito", "veel", "たくさん (takusan)", "很多 (hěnduō)"),
    "many": ("muchos", "beaucoup", "viele", "molti", "muitos", "veel", "たくさん (takusan)", "很多 (hěnduō)"),
    # numbers
    "one": ("uno", "un", "eins", "uno", "um", "een", "一 (ichi)", "一 (yī)"),
    "two": ("dos", "deux", "zwei", "due", "dois", "twee", "二 (ni)", "二 (èr)"),
    "three": ("tres", "trois", "drei", "tre", "três", "drie", "三 (san)", "三 (sān)"),
    "four": ("cuatro", "quatre", "vier", "quattro", "quatro", "vier", "四 (yon)", "四 (sì)"),
    "five": ("cinco", "cinq", "fünf", "cinque", "cinco", "vijf", "五 (go)", "五 (wǔ)"),
    "ten": ("diez", "dix", "zehn", "dieci", "dez", "tien", "十 (jū)", "十 (shí)"),
    # time words
    "today": ("hoy", "aujourd'hui", "heute", "oggi", "hoje", "vandaag", "今日 (kyō)", "今天 (jīntiān)"),
    "tomorrow": ("mañana", "demain", "morgen", "domani", "amanhã", "morgen", "明日 (ashita)", "明天 (míngtiān)"),
    "yesterday": ("ayer", "hier", "gestern", "ieri", "ontem", "gisteren", "昨日 (kinō)", "昨天 (zuótiān)"),
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

    # 2) Word-by-word gloss over the whole sentence. Any word we don't have is
    #    left as-is; the caller shows which words were translated so the reader
    #    knows this is a literal rendering, not fluent output.
    words = re.findall(r"[\w']+|[^\w\s]", phrase_raw.strip())
    if len([w for w in words if w.isalpha()]) > 40:
        return None
    per_word: list[tuple[str, str, bool]] = []
    known = 0
    for token in words:
        low = token.lower()
        if not token.isalpha():
            per_word.append((token, token, True))    # punctuation, keep as-is
            continue
        if low in _PHRASES:
            per_word.append((token, _PHRASES[low][column], True))
            known += 1
        elif low in _WORDS:
            translated = _WORDS[low][column]
            # Some cells are intentionally empty (e.g. Japanese has no article
            # for "the"); drop those from the output rather than show a gap.
            per_word.append((token, translated or "", bool(translated)))
            if translated:
                known += 1
        else:
            per_word.append((token, token, False))    # unknown, pass through
    if known == 0:
        return None

    # Assemble, skipping the empty (dropped-article) tokens.
    glossed = " ".join(t for _, t, _ in per_word if t).strip()
    glossed = re.sub(r"\s+([,.!?;:])", r"\1", glossed)
    return Translation(
        source=phrase_raw.strip(), target_code=code, target_name=name,
        target_endonym=endonym, result=glossed, literal=True,
        per_word=[(s, t) for s, t, matched in per_word if s.isalpha()],
    )
