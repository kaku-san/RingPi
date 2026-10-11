"""Door evidence for a temporary guest. A probable match is not identity and cannot actuate."""

GUEST_STATES = {"probable_match", "uncertain", "unknown", "not_enrolled", "no_face",
                "low_quality", "multiple_faces", "unavailable", "not_compared", "pending"}


def _hex_id(value):
    return isinstance(value, str) and len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def aggregate_guest(frames, complete, expected):
    """Agreeing frames can name a candidate visitor. They never grant entry."""
    observations = []
    for frame in frames:
        guest = frame.get("guest") if isinstance(frame.get("guest"), dict) else None
        if not guest:
            observations.append({"status": "not_compared", "visitor_id": None, "similarity": None})
            continue
        status = guest.get("status") if guest.get("status") in GUEST_STATES else "unavailable"
        visitor = guest.get("visitor_id") if status == "probable_match" and _hex_id(guest.get("visitor_id")) else None
        # A worker-supplied authority flag is discarded and vetoes the candidate.
        if guest.get("identity_proven") or guest.get("authorizes_entry") or status == "probable_match" and visitor is None:
            status, visitor = "uncertain", None
        similarity = guest.get("similarity")
        if isinstance(similarity, bool) or not isinstance(similarity, (int, float)):
            similarity = None
        observations.append({"status": status, "visitor_id": visitor,
                             "similarity": similarity if status == "probable_match" else None})
    states = {item["status"] for item in observations}
    matches = [item for item in observations if item["status"] == "probable_match" and item["visitor_id"]]
    visitors = {item["visitor_id"] for item in matches}
    final = bool(complete) and len(frames) == expected and expected > 0
    if "multiple_faces" in states:
        status = "multiple_faces"
    elif len(visitors) > 1 or (matches and states & {"unknown", "uncertain", "not_enrolled"}):
        status = "uncertain"
    elif matches and ("unavailable" in states or not final):
        status = "uncertain" if "unavailable" in states or (complete and not final) else "pending"
    elif matches and final and len(matches) == len(frames) and len(visitors) == 1:
        status = "probable_match"
    elif matches:
        status = "uncertain"
    else:
        status = next((state for state in ("uncertain", "unknown", "not_enrolled", "unavailable",
                       "low_quality", "no_face", "not_compared") if state in states), "not_compared")
    similarities = [item["similarity"] for item in matches if isinstance(item["similarity"], (int, float))]
    return {"status": status,
            "visitor_id": next(iter(visitors)) if status == "probable_match" else None,
            "similarity": min(similarities) if status == "probable_match" and similarities else None,
            "identity_proven": False, "authorizes_entry": False, "display": "pending"}


def sighting_from_analysis(analysis):
    """Return a candidate only when aggregated evidence does not claim authority."""
    if not isinstance(analysis, dict):
        return None
    guest = analysis.get("guest")
    if not isinstance(guest, dict) or guest.get("identity_proven") or guest.get("authorizes_entry"):
        return None
    if guest.get("status") != "probable_match" or guest.get("display") not in {None, "pending"}:
        return None
    if not _hex_id(guest.get("visitor_id")):
        return None
    similarity = guest.get("similarity")
    if isinstance(similarity, bool) or not isinstance(similarity, (int, float)):
        similarity = None
    return guest["visitor_id"], similarity
