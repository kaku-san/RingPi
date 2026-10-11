"""Factual sentences from local detections, not a vision-language scene caption."""
from .object_labels import OBJECT_LABELS


def object_labels(event):
    return list(dict.fromkeys(o["label"] for o in (event.get("local_analysis") or {}).get("objects", [])
                              if o.get("label") in OBJECT_LABELS))[:10]


def clean_name(name):
    if not isinstance(name, str):
        return None
    cleaned = "".join(ch for ch in " ".join(name.split()) if ord(ch) >= 32 and ord(ch) != 127)[:60]
    return cleaned or None


def probable_line(name, score):
    """One dashboard/webhook line. A score is not proof of identity."""
    cleaned = clean_name(name)
    if not cleaned or isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return f"Probably {cleaned} ({float(score):.2f})"


def observation_summary(objects, person_status, face_status, identity_line=None):
    """Never turn missing detections into scene claims or verified identity."""
    labels = list(objects)
    if "person" not in labels and (person_status in {"detected", "probable_match"}
            or face_status in {"probable_match", "unknown", "uncertain", "low_quality", "multiple_faces", "not_enrolled"}):
        labels.insert(0, "person")
    elif "person" in labels:
        labels.remove("person")
        labels.insert(0, "person")
    sentences = []
    if labels:
        plural = {"skis", "scissors"}
        nouns = [label if label in plural else ("an " if label[0] in "aeiou" else "a ") + label for label in labels]
        subject = nouns[0] if len(nouns) == 1 else ", ".join(nouns[:-1]) + " and " + nouns[-1]
        sentences.append(subject[0].upper() + subject[1:] + (" was detected." if len(nouns) == 1 and labels[0] not in plural else " were detected."))
    if identity_line:
        sentences.append(identity_line)
    elif "person" in labels and face_status in {
            "low_quality", "no_face", "probable_match", "unknown", "uncertain", "multiple_faces", "not_enrolled"}:
        sentences.append("unknown")
    return " ".join(sentences)


def local_description(event):
    face = event.get("face_status")
    person = event.get("person_status")
    line = None
    if face == "probable_match" and person == "probable_match":
        score = event.get("face_similarity")
        analysis = event.get("local_analysis")
        if score is None and isinstance(analysis, dict):
            score = analysis.get("similarity")
        line = probable_line(event.get("person_name"), score) or "unknown"
    text = observation_summary(object_labels(event), person, face, line)
    if text:
        return text, "ready"
    if not event.get("frames"):
        return None, "pending" if event.get("capture_status") == "pending" else "unavailable"
    if not event.get("local_analysis") and event.get("person_status") == "unavailable":
        return None, "unavailable"
    return None, "no_confident_observations"
