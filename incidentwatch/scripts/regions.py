"""Sydney regions = groups of council areas (LGAs)."""
import re
REGIONS = {
    "northern-beaches": ("Northern Beaches", ["Northern Beaches"]),
    "north-shore": ("North Shore & Ryde", ["North Sydney", "Willoughby", "Lane Cove", "Mosman", "Ku-ring-gai", "Hunters Hill", "Ryde"]),
    "hills-hornsby": ("Hills & Hornsby", ["Hornsby", "The Hills"]),
    "city-inner-west": ("City & Inner West", ["Sydney", "Inner West", "Canada Bay", "Burwood", "Strathfield"]),
    "eastern-suburbs": ("Eastern Suburbs", ["Randwick", "Waverley", "Woollahra"]),
    "st-george-sutherland": ("St George & Sutherland", ["Bayside", "Botany Bay", "Rockdale", "Georges River", "Sutherland"]),
    "canterbury-bankstown": ("Canterbury-Bankstown", ["Canterbury-Bankstown"]),
    "western-sydney": ("Parramatta, Cumberland & Blacktown", ["Parramatta", "Cumberland", "Blacktown"]),
    "south-west": ("South West Sydney", ["Liverpool", "Fairfield", "Campbelltown", "Camden", "Wollondilly"]),
    "penrith-mountains": ("Penrith, Hawkesbury & Blue Mountains", ["Penrith", "Hawkesbury", "Blue Mountains"]),
    "central-coast": ("Central Coast", ["Central Coast"]),
}
LGA_TO_REGION = {lga.lower(): rid for rid, (_, lgas) in REGIONS.items() for lga in lgas}

def clean_lga(name):
    n = re.sub(r"\s*\([^)]*\)\s*", " ", name or "")
    n = re.sub(r"\b(Shire|Council|City of|Municipal|Regional)\b", " ", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    return n

def region_for_lga(name):
    return LGA_TO_REGION.get(clean_lga(name).lower())
