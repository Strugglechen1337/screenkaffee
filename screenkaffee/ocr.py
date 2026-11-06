"""Bundled OCR and editable sensitive-data masks. Recognition runs locally."""
from dataclasses import dataclass
import re
import threading
from .paths import application_root

_LOCK = threading.Lock()


@dataclass
class Word:
    text: str
    box: tuple
    confidence: float


@dataclass
class Recognition:
    text: str
    words: list


def recognize(image, language="deu+eng"):
    import tesserocr
    folder = application_root() / "models" / "tessdata"
    requested = language.split("+")
    if not requested or any(not re.fullmatch(r"[A-Za-z_]+", name) for name in requested):
        raise ValueError("Ungültige OCR-Sprachkennung.")
    missing = [name for name in requested if not (folder / (name + ".traineddata")).is_file()]
    if missing:
        raise RuntimeError("OCR-Sprachdateien fehlen: " + ", ".join(missing))
    # Tesseract has some process-global configuration; serialize initialisation
    # and recognition instead of racing independent worker calls.
    with _LOCK, tesserocr.PyTessBaseAPI(path=str(folder), lang=language,
            oem=tesserocr.OEM.LSTM_ONLY, psm=tesserocr.PSM.AUTO) as api:
        api.SetImage(image.convert("RGB"))
        api.Recognize()
        text = api.GetUTF8Text()
        words = []
        iterator = api.GetIterator()
        if iterator:
            for item in tesserocr.iterate_level(iterator, tesserocr.RIL.WORD):
                value = item.GetUTF8Text(tesserocr.RIL.WORD)
                box = item.BoundingBox(tesserocr.RIL.WORD)
                if value and box:
                    words.append(Word(value.strip(), tuple(box), item.Confidence(tesserocr.RIL.WORD)))
        return Recognition(text, words)


_EMAIL = re.compile(r"[\w.+%-]+\s*@\s*[\w.-]+\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b")


_PHONE = re.compile(r"(?<![\w.])(?:\+|00)\d{1,3}[\s/.-]?\(?\d{1,5}\)?(?:[\s/.-]?\d{2,}){2,4}(?!\w)|(?<![\w.])0\d{2,5}[\s/.-]\d{3,}(?:[\s/.-]?\d+)*(?!\w)")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}(?![\w:])|(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}(?![\w:])")
_URL_CREDENTIALS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)
_SECRET_LABEL = re.compile(r"\b(?:passwort|password|kennwort|passwd|pwd|secret|geheimnis|pin|api[ _-]?key)\b\s*[:=]\s*\S{3,}", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*", re.IGNORECASE)
_US_SSN = re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)")


def luhn_ok(digits):
    total, flip = 0, False
    for char in reversed(digits):
        value = int(char)
        if flip:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
        flip = not flip
    return total % 10 == 0


def is_sensitive(text):
    """Heuristic: email, IBAN, payment card (Luhn), phone number, IPv4/IPv6 address, API token,
    bearer token, private-key header, URL with credentials, labelled password/PIN or US SSN."""
    if _EMAIL.search(text) or _IBAN.search(text.replace(" ", "")) or _TOKEN.search(text) or _IPV4.search(text):
        return True
    if any(pattern.search(text) for pattern in (_IPV6, _URL_CREDENTIALS, _SECRET_LABEL, _PRIVATE_KEY, _BEARER, _US_SSN)):
        return True
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return True
    return bool(_PHONE.search(text))


def sensitive_regions(recognition):
    """Detect candidate emails/IBANs; return editable boxes for user review."""
    words = recognition.words
    boxes = []
    for start in range(len(words)):
        for count in range(1, min(10, len(words) - start) + 1):
            group = words[start:start + count]
            # Do not accidentally join different lines into a secret.
            top, bottom = max(w.box[1] for w in group), min(w.box[3] for w in group)
            if bottom <= top:
                break
            text = " ".join(w.text for w in group)
            if not is_sensitive(text):
                continue
            bounds = (min(w.box[0] for w in group) - 3, min(w.box[1] for w in group) - 3,
                      max(w.box[2] for w in group) + 3, max(w.box[3] for w in group) + 3)
            if not any(bounds[0] >= b[0] and bounds[1] >= b[1] and bounds[2] <= b[2] and bounds[3] <= b[3] for b in boxes):
                boxes.append(bounds)
            break
    return boxes
