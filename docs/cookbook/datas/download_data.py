#!/usr/bin/env python3
"""Download multimodal sample data for Arrow Lake examples.

Downloads real-world images, videos, and texts from public sources
(Wikimedia Commons, Project Gutenberg) for use in tutorials.

Usage:
    python download_data.py                # Download all
    python download_data.py --small        # Download all (skip large videos)
    python download_data.py --images-only  # Images only
    python download_data.py --videos-only  # Videos only
    python download_data.py --audio-only   # Audio only
    python download_data.py --texts-only   # Texts only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

DATA_DIR = Path(__file__).parent

USER_AGENT = "ArrowLakeDataDownloader/1.0 (tutorial data fetcher)"
WIKIMEDIA_BASE = "https://upload.wikimedia.org/wikipedia/commons"
GUTENBERG_BASE = "https://www.gutenberg.org/files"

REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 5
RETRY_BACKOFF = 4  # seconds (doubled each retry)
REQUEST_DELAY = 1  # seconds between Wikimedia requests (rate limit)


# ---------------------------------------------------------------------------
# Image definitions: (category, local_filename, commons_hash_path, commons_filename)
# commons_hash_path = "{h1}/{h2}" where h1 = first char, h2 = first two chars
# Thumbnail URL: {WIKIMEDIA_BASE}/thumb/{hash_path}/{filename}/1280px-{filename}
# ---------------------------------------------------------------------------

IMAGES: list[dict[str, str]] = [
    # ── NATURE ──────────────────────────────────────────────────────────────
    {
        "category": "nature",
        "filename": "perito_moreno_glacier.jpg",
        "hash_path": "5/5c",
        "commons_name": "Perito_Moreno_Glacier_Patagonia_Argentina_Luca_Galuzzi_2005.JPG",
        "description": "Perito Moreno Glacier, Patagonia, Argentina",
    },
    {
        "category": "nature",
        "filename": "everest_north_face.jpg",
        "hash_path": "e/e7",
        "commons_name": "Everest_North_Face_toward_Base_Camp_Tibet_Luca_Galuzzi_2006.jpg",
        "description": "Mount Everest North Face, Tibet",
    },
    {
        "category": "nature",
        "filename": "fitz_roy.jpg",
        "hash_path": "5/53",
        "commons_name": "Fitz_Roy_1.jpg",
        "description": "Mount Fitz Roy, Argentina",
    },
    {
        "category": "nature",
        "filename": "laguna_colorada.jpg",
        "hash_path": "a/a0",
        "commons_name": "Laguna_Colorada_MC.jpg",
        "description": "Laguna Colorada, Bolivia",
    },
    {
        "category": "nature",
        "filename": "dead_vlei.jpg",
        "hash_path": "c/c0",
        "commons_name": "Dead_Vlei_4.jpg",
        "description": "Dead Vlei, Namibia",
    },
    {
        "category": "nature",
        "filename": "sossusvlei_dune.jpg",
        "hash_path": "4/45",
        "commons_name": "Sossusvlei_Dune_Namib_Desert_Namibia_Luca_Galuzzi_2004.JPG",
        "description": "Sossusvlei sand dunes, Namibia",
    },
    {
        "category": "nature",
        "filename": "fryxellsee.jpg",
        "hash_path": "8/8f",
        "commons_name": "Fryxellsee_Opt.jpg",
        "description": "Lake Fryxell, Antarctica",
    },
    {
        "category": "nature",
        "filename": "ngorongoro_crater.jpg",
        "hash_path": "3/35",
        "commons_name": "Inside_Ngorongoro_crater.jpg",
        "description": "Ngorongoro Crater, Tanzania",
    },
    {
        "category": "nature",
        "filename": "skogafoss.jpg",
        "hash_path": "9/98",
        "commons_name": "Sk%C3%B3gafoss_July_2014.JPG",
        "description": "Skogafoss waterfall, Iceland",
    },
    {
        "category": "nature",
        "filename": "victoria_falls.jpg",
        "hash_path": "1/13",
        "commons_name": "Cataratas_Victoria%2C_Zambia-Zimbabue%2C_2018-07-27%2C_DD_16-20_PAN.jpg",
        "description": "Victoria Falls, Zambia-Zimbabwe",
    },
    # ── ARCHITECTURE ────────────────────────────────────────────────────────
    {
        "category": "architecture",
        "filename": "bolshoi_theatre.jpg",
        "hash_path": "1/12",
        "commons_name": "Moscow_-_2025_-_Facade_of_Big_Theatre_%281%29.jpg",
        "description": "Bolshoi Theatre, Moscow",
    },
    {
        "category": "architecture",
        "filename": "colosseum.jpg",
        "hash_path": "5/53",
        "commons_name": "Colosseum_in_Rome%2C_Italy_-_April_2007.jpg",
        "description": "Colosseum, Rome, Italy",
    },
    {
        "category": "architecture",
        "filename": "eiffel_tower.jpg",
        "hash_path": "a/a8",
        "commons_name": "Tour_Eiffel_Wikimedia_Commons.jpg",
        "description": "Eiffel Tower, Paris",
    },
    {
        "category": "architecture",
        "filename": "taj_mahal.jpg",
        "hash_path": "7/74",
        "commons_name": "Taj_Mahal%2C_Agra%2C_India_edit2.jpg",
        "description": "Taj Mahal, Agra, India",
    },
    {
        "category": "architecture",
        "filename": "sagrada_familia.jpg",
        "hash_path": "b/b9",
        "commons_name": "Sagrada_Familia_March_2015-19bw.jpg",
        "description": "Sagrada Familia, Barcelona",
    },
    {
        "category": "architecture",
        "filename": "machu_picchu.jpg",
        "hash_path": "6/62",
        "commons_name": "80_-_Machu_Picchu_-_Juin_2009_-_edit.jpg",
        "description": "Machu Picchu, Peru",
    },
    {
        "category": "architecture",
        "filename": "frick_building.jpg",
        "hash_path": "a/a6",
        "commons_name": "Auditorium_in_the_Frick_Fine_Arts_Building.jpg",
        "description": "Frick Fine Arts Building, Pittsburgh",
    },
    {
        "category": "architecture",
        "filename": "wredes_passage.jpg",
        "hash_path": "1/10",
        "commons_name": "Wredes_passage_November_2025_01.jpg",
        "description": "Wredes passage",
    },
    {
        "category": "architecture",
        "filename": "pointe_saint_mathieu.jpg",
        "hash_path": "4/46",
        "commons_name": "Pointe_Saint_Mathieu_-_Phare.jpg",
        "description": "Pointe Saint Mathieu lighthouse, France",
    },
    {
        "category": "architecture",
        "filename": "pyrenees_andorra.jpg",
        "hash_path": "d/d6",
        "commons_name": "Pyrenees_in_Andorra_%2810%29.jpg",
        "description": "Pyrenees, Andorra",
    },
    # ── ANIMALS ─────────────────────────────────────────────────────────────
    {
        "category": "animals",
        "filename": "brown_violetear.jpg",
        "hash_path": "4/48",
        "commons_name": "Brown_violetear_%28Colibri_delphinae%29.jpg",
        "description": "Brown violetear hummingbird",
    },
    {
        "category": "animals",
        "filename": "christmas_tree_worm.jpg",
        "hash_path": "0/0a",
        "commons_name": "Spirobranchus_giganteus.jpg",
        "description": "Christmas tree worm (Spirobranchus giganteus)",
    },
    {
        "category": "animals",
        "filename": "moon_jellyfish.jpg",
        "hash_path": "f/f9",
        "commons_name": "Aurelia_aurita_%28Cnidaria%29_Luc_Viatour.jpg",
        "description": "Moon jellyfish (Aurelia aurita)",
    },
    {
        "category": "animals",
        "filename": "lions_mane_jellyfish.jpg",
        "hash_path": "d/d9",
        "commons_name": "Lion%27s_mane_jellyfish_in_Gullmarn_fjord_at_S%C3%A4mstad_3.jpg",
        "description": "Lion's mane jellyfish",
    },
    {
        "category": "animals",
        "filename": "coconut_octopus.jpg",
        "hash_path": "8/80",
        "commons_name": "Coconut_octopus_%28Amphioctopus_marginatus%29_%2845031078485%29.jpg",
        "description": "Coconut octopus",
    },
    {
        "category": "animals",
        "filename": "blue_glaucus.jpg",
        "hash_path": "5/51",
        "commons_name": "Sea_Swallow_Glaucus_atlanticus.jpg",
        "description": "Blue sea slug (Glaucus atlanticus)",
    },
    {
        "category": "animals",
        "filename": "roman_snail.jpg",
        "hash_path": "8/8c",
        "commons_name": "Helix_pomatia_june01.JPG",
        "description": "Roman snail (Helix pomatia)",
    },
    {
        "category": "animals",
        "filename": "stovepipe_sponge.jpg",
        "hash_path": "2/20",
        "commons_name": "Aplysina_archeri_%28Stove-pipe_Sponge-pink_variation%29.jpg",
        "description": "Stove-pipe sponge (Aplysina archeri)",
    },
    {
        "category": "animals",
        "filename": "granulated_starfish.jpg",
        "hash_path": "6/66",
        "commons_name": "Estrella_de_mar_granulada_%28Choriaster_granulatus%29%2C_Zanz%C3%ADbar%2C_Tanzania%2C_2024-05-31%2C_DD_67.jpg",
        "description": "Granulated sea star (Choriaster granulatus)",
    },
    {
        "category": "animals",
        "filename": "great_egret.jpg",
        "hash_path": "1/10",
        "commons_name": "012_Great_egret_fishing_during_a_foggy_day_at_Champ-Pittet_Photo_by_Giles_Laurent.jpg",
        "description": "Great egret fishing",
    },
    # ── FOOD ────────────────────────────────────────────────────────────────
    {
        "category": "food",
        "filename": "four_pears.jpg",
        "hash_path": "9/99",
        "commons_name": "Four_pears.jpg",
        "description": "Four pears",
    },
    {
        "category": "food",
        "filename": "citrus_fruits.jpg",
        "hash_path": "e/e0",
        "commons_name": "Citrus_fruits.jpg",
        "description": "Assorted citrus fruits",
    },
    {
        "category": "food",
        "filename": "raspberries.jpg",
        "hash_path": "6/69",
        "commons_name": "Raspberries05.jpg",
        "description": "Fresh raspberries",
    },
    {
        "category": "food",
        "filename": "pomegranate.jpg",
        "hash_path": "9/9b",
        "commons_name": "Pomegranate02_edit.jpg",
        "description": "Pomegranate seeds",
    },
    {
        "category": "food",
        "filename": "kiwi_fruit.jpg",
        "hash_path": "b/b8",
        "commons_name": "Kiwi_%28Actinidia_chinensis%29_1_Luc_Viatour.jpg",
        "description": "Kiwi fruit (Actinidia chinensis)",
    },
    {
        "category": "food",
        "filename": "coffee_beans.jpg",
        "hash_path": "c/c5",
        "commons_name": "Roasted_coffee_beans.jpg",
        "description": "Roasted coffee beans",
    },
    {
        "category": "food",
        "filename": "romanesco_broccoli.jpg",
        "hash_path": "5/5e",
        "commons_name": "Romanesco_broccoli_%28Brassica_oleracea%29.jpg",
        "description": "Romanesco broccoli",
    },
    {
        "category": "food",
        "filename": "garlic.jpg",
        "hash_path": "9/9a",
        "commons_name": "Garlic_bulbs_and_cloves.jpg",
        "description": "Garlic bulbs and cloves",
    },
    {
        "category": "food",
        "filename": "star_anise.jpg",
        "hash_path": "2/2f",
        "commons_name": "Dried_Star_Anise_Fruit_Seeds.jpg",
        "description": "Dried star anise",
    },
    {
        "category": "food",
        "filename": "cardamom_buns.jpg",
        "hash_path": "7/72",
        "commons_name": "Cardamom_buns.jpg",
        "description": "Swedish cardamom buns",
    },
    # ── TECHNOLOGY / SCIENCE ────────────────────────────────────────────────
    {
        "category": "technology",
        "filename": "emission_nebulae.jpg",
        "hash_path": "f/f9",
        "commons_name": "Emission_nebulae_in_Cepheus_and_Cassiopeia.jpg",
        "description": "Emission nebulae in Cepheus and Cassiopeia",
    },
    {
        "category": "technology",
        "filename": "wr134_star.png",
        "hash_path": "b/b4",
        "commons_name": "WR-134.png",
        "description": "WR-134 Wolf-Rayet star",
    },
    {
        "category": "technology",
        "filename": "nasa_ingenuity.jpg",
        "hash_path": "c/c8",
        "commons_name": "NASA%27s_Ingenuity_helicopter_on_Mars_by_Perseverance_Mastcam-Z%2C_Sol_768_%2853475637956%29.jpg",
        "description": "NASA Ingenuity helicopter on Mars",
    },
    {
        "category": "technology",
        "filename": "ritchiey_chretien_telescope.jpg",
        "hash_path": "7/74",
        "commons_name": "Ritchey%E2%80%93Chr%C3%A9tien_at_Kickapoo_Valley_Reserve_2-b.jpg",
        "description": "Ritchey-Chretien telescope",
    },
    {
        "category": "technology",
        "filename": "apollo_17_tracys_rock.jpg",
        "hash_path": "2/2c",
        "commons_name": "Apollo_17_Harrison_H._Schmitt_and_Tracy%27s_Rock_-_AS17-140-21493%2BAS17-140-21497_2025.jpg",
        "description": "Apollo 17 Tracy's Rock",
    },
    {
        "category": "technology",
        "filename": "atmosphere_composition.svg",
        "hash_path": "a/a3",
        "commons_name": "Atmosphere_composition_diagram-en.svg",
        "description": "Earth atmosphere composition diagram",
    },
    {
        "category": "technology",
        "filename": "c_elegans.jpg",
        "hash_path": "8/82",
        "commons_name": "Caenorhabditis_elegans.jpg",
        "description": "C. elegans nematode",
    },
    {
        "category": "technology",
        "filename": "hiv_virus_sem.jpg",
        "hash_path": "b/b0",
        "commons_name": "Scanning_electron_micrograph_of_a_human_H9_T_cell_infected_with_HIV_virus_particles.jpg",
        "description": "HIV virus particles (SEM)",
    },
    {
        "category": "technology",
        "filename": "gigantic_jet.jpg",
        "hash_path": "6/6a",
        "commons_name": "Gigantic_jet_NOIRLab.jpg",
        "description": "Gigantic jet lightning",
    },
    {
        "category": "technology",
        "filename": "northern_sky_survey.jpg",
        "hash_path": "d/d2",
        "commons_name": "Northern_Sky_Narrowband_Survey_-_True_colors.jpg",
        "description": "Northern Sky Narrowband Survey",
    },
]


# ---------------------------------------------------------------------------
# Video definitions: (local_filename, commons_url, description, license)
# ---------------------------------------------------------------------------

VIDEOS: list[dict[str, str]] = [
    {
        "filename": "bbb_bird_clip.ogv",
        "url": f"{WIKIMEDIA_BASE}/c/cf/Big_Buck_Bunny_8_seconds_bird_clip.ogv",
        "description": "Big Buck Bunny - 8 second bird clip (720p)",
        "license": "CC BY 3.0 (Blender Foundation)",
        "small": True,
    },
    {
        "filename": "bbb_montage.ogv",
        "url": f"{WIKIMEDIA_BASE}/1/1b/Big_buck_bunny_montage.ogv",
        "description": "Big Buck Bunny montage (576p, 15s)",
        "license": "CC BY 3.0 (Blender Foundation)",
        "small": True,
    },
    {
        "filename": "bbb_trailer_1080p.ogv",
        "url": f"{WIKIMEDIA_BASE}/1/18/Big_Buck_Bunny_Trailer_1080p.ogv",
        "description": "Big Buck Bunny trailer (1080p, 33s)",
        "license": "CC BY 3.0 (Blender Foundation)",
        "small": False,
    },
    {
        "filename": "bbb_first_23s.ogv",
        "url": f"{WIKIMEDIA_BASE}/f/f3/Big_Buck_Bunny_first_23_seconds_1080p.ogv",
        "description": "Big Buck Bunny first 23 seconds (1080p)",
        "license": "CC BY 3.0 (Blender Foundation)",
        "small": False,
    },
    {
        "filename": "sintel_trailer.ogv",
        "url": f"{WIKIMEDIA_BASE}/0/06/Sintel_trailer-1080p.ogv",
        "description": "Sintel trailer (1080p, 52s)",
        "license": "CC BY 3.0 (Blender Foundation)",
        "small": False,
    },
]


# ---------------------------------------------------------------------------
# Text definitions: (local_filename, gutenberg_id, title, author, language)
# ---------------------------------------------------------------------------

TEXTS: list[dict[str, str]] = [
    {
        "filename": "alice_in_wonderland.txt",
        "gutenberg_id": "11",
        "title": "Alice's Adventures in Wonderland",
        "author": "Lewis Carroll",
        "language": "en",
    },
    {
        "filename": "peter_pan.txt",
        "gutenberg_id": "16",
        "title": "Peter Pan",
        "author": "J. M. Barrie",
        "language": "en",
    },
    {
        "filename": "art_of_war.txt",
        "gutenberg_id": "132",
        "title": "The Art of War",
        "author": "Sun Tzu",
        "language": "en",
    },
    {
        "filename": "frankenstein.txt",
        "gutenberg_id": "84",
        "title": "Frankenstein; or, The Modern Prometheus",
        "author": "Mary Shelley",
        "language": "en",
    },
    {
        "filename": "dorian_gray.txt",
        "gutenberg_id": "174",
        "title": "The Picture of Dorian Gray",
        "author": "Oscar Wilde",
        "language": "en",
    },
    {
        "filename": "tale_of_two_cities.txt",
        "gutenberg_id": "98",
        "title": "A Tale of Two Cities",
        "author": "Charles Dickens",
        "language": "en",
    },
    {
        "filename": "metamorphosis.txt",
        "gutenberg_id": "5200",
        "title": "Metamorphosis",
        "author": "Franz Kafka",
        "language": "de",
    },
    {
        "filename": "pride_and_prejudice.txt",
        "gutenberg_id": "1342",
        "title": "Pride and Prejudice",
        "author": "Jane Austen",
        "language": "en",
    },
    {
        "filename": "moby_dick.txt",
        "gutenberg_id": "2701",
        "title": "Moby-Dick; or, The Whale",
        "author": "Herman Melville",
        "language": "en",
    },
    {
        "filename": "don_quixote.txt",
        "gutenberg_id": "2000",
        "title": "Don Quixote",
        "author": "Miguel de Cervantes",
        "language": "en",
    },
]


# ---------------------------------------------------------------------------
# Audio definitions: diverse sound types from Wikimedia Commons (CC BY / CC0)
# ---------------------------------------------------------------------------

AUDIOS: list[dict[str, str]] = [
    # ── ANIMAL SOUNDS ──────────────────────────────────────────────────────
    {
        "category": "animals",
        "filename": "cat_meow.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/0/0c/Meow_domestic_cat.ogg"
        ),
        "description": "Domestic cat meow",
        "license": "CC BY 3.0",
    },
    {
        "category": "animals",
        "filename": "tropical_bird_song.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/1/1b/"
            "Singing-in-the-Rain-Forest-How-a-Tropical-Bird-Song-Transfers"
            "-Information-pone.0001580.s007.ogg"
        ),
        "description": "Tropical bird song (rain forest)",
        "license": "CC BY 3.0",
    },
    # ── MUSICAL INSTRUMENTS ────────────────────────────────────────────────
    {
        "category": "instruments",
        "filename": "violin_chords.ogg",
        "url": f"{WIKIMEDIA_BASE}/f/f6/Violin_chords.ogg",
        "description": "Violin chord demonstrations",
        "license": "CC BY-SA 3.0",
    },
    {
        "category": "instruments",
        "filename": "piano_i_love_you.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/4/42/"
            "Cole_Porter%E2%80%99s_%E2%80%9CI_Love_You%E2%80%9D"
            "_-_JMC%2C_Han%27s_Piano_Edition.ogg"
        ),
        "description": "Piano — Cole Porter's I Love You",
        "license": "CC BY 3.0",
    },
    {
        "category": "instruments",
        "filename": "trumpet_bflat.ogg",
        "url": f"{WIKIMEDIA_BASE}/3/38/Natural_trumpet_B-flat.ogg",
        "description": "Natural trumpet in B-flat",
        "license": "CC BY-SA 3.0",
    },
    {
        "category": "instruments",
        "filename": "sax_growl.ogg",
        "url": f"{WIKIMEDIA_BASE}/6/6a/Sax_growl.ogg",
        "description": "Saxophone growl technique",
        "license": "CC BY 3.0",
    },
    {
        "category": "instruments",
        "filename": "drum_cadence.ogg",
        "url": f"{WIKIMEDIA_BASE}/e/e1/Drum_-_Cadence_A.ogg",
        "description": "Drum cadence A (percussion)",
        "license": "CC BY-SA 3.0",
    },
    # ── CLASSICAL MUSIC ────────────────────────────────────────────────────
    {
        "category": "music",
        "filename": "beethoven_moonlight_sonata.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/3/3f/"
            "Moonlight_Sonata_%28Sharp_c_minor_Sonata%29_2nd_Movement"
            "_Beethoven_JMC%2CHan.ogg"
        ),
        "description": "Beethoven Moonlight Sonata, 2nd movement",
        "license": "CC BY 3.0",
    },
    {
        "category": "music",
        "filename": "bach_cello_suite.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/4/43/"
            "JOHN_MICHEL_CELLO-J_S_BACH_CELLO_SUITE_1_in_G_Prelude.ogg"
        ),
        "description": "Bach Cello Suite No.1 Prelude",
        "license": "CC BY 3.0",
    },
    # ── NATURE SOUNDS ──────────────────────────────────────────────────────
    {
        "category": "nature",
        "filename": "rain_and_thunder.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/b/bb/"
            "Rain_and_thunder_%281%29.ogg"
        ),
        "description": "Rain and thunder storm",
        "license": "CC BY 3.0",
    },
    # ── SPOKEN WORD ─────────────────────────────────────────────────────────
    {
        "category": "speech",
        "filename": "tedx_speech_excerpt.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/c/cb/"
            "Stephen_Cobb_speaking_about_White_Male_Effect"
            "_at_TEDx_San_Diego_2015.ogg"
        ),
        "description": "TEDx speech excerpt (spoken English)",
        "license": "CC BY 3.0",
    },
    {
        "category": "speech",
        "filename": "en_us_food.wav",
        "url": f"{WIKIMEDIA_BASE}/c/c9/En_US_Food.wav",
        "description": 'US English pronunciation of "food"',
        "license": "CC BY-SA 3.0",
    },
    # ── SOUND EFFECTS ──────────────────────────────────────────────────────
    {
        "category": "effects",
        "filename": "gong.ogg",
        "url": f"{WIKIMEDIA_BASE}/2/2c/Gong55.ogg",
        "description": "Gong sound (55Hz)",
        "license": "CC0",
    },
    {
        "category": "effects",
        "filename": "church_bell.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/5/5a/"
            "Samariter_Church_Bell_III_%28b%29.ogg"
        ),
        "description": "Church bell peal",
        "license": "CC BY-SA 3.0",
    },
    {
        "category": "effects",
        "filename": "electric_guitar_humbucker.ogg",
        "url": (
            f"{WIKIMEDIA_BASE}/e/ee/"
            "Electric_guitar_neck_humbucker_%28hotrail%29"
            "_-_full_vs_split.ogg"
        ),
        "description": "Electric guitar humbucker pickup",
        "license": "CC BY-SA 3.0",
    },
]


# ---------------------------------------------------------------------------
# Download utilities
# ---------------------------------------------------------------------------


def _download_url(url: str, dest: Path, delay: float = 0) -> bool:
    """Download a URL to dest with retry logic. Returns True on success."""
    if delay > 0:
        time.sleep(delay)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Ensure URL is properly encoded (handle parentheses, spaces, etc.)
            parsed = urllib.parse.urlparse(url)
            # Decode any existing percent-encoding first, then re-encode
            decoded_path = urllib.parse.unquote(parsed.path)
            encoded_path = urllib.parse.quote(decoded_path, safe="/")
            safe_url = parsed._replace(path=encoded_path).geturl()
            req = urllib.request.Request(safe_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = resp.read()
                dest.write_bytes(data)
                return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Wikimedia rate limit — wait longer
                wait = RETRY_BACKOFF * (2 ** min(attempt, 4))
                if attempt < MAX_RETRIES:
                    print(f"    429 rate-limited, waiting {wait}s...", flush=True)
                    time.sleep(wait)
                else:
                    print(f"    FAILED: rate limited after {MAX_RETRIES} retries")
                    return False
            elif e.code >= 500:
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                if attempt < MAX_RETRIES:
                    print(f"    {e.code} error, retry {attempt}/{MAX_RETRIES} in {wait}s")
                    time.sleep(wait)
                else:
                    print(f"    FAILED: {e}")
                    return False
            else:
                print(f"    FAILED: HTTP {e.code}")
                return False
        except (urllib.error.URLError, OSError) as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                print(f"    Retry {attempt}/{MAX_RETRIES} after {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"    FAILED: {e}")
                return False
    return False


def download_images() -> list[dict]:
    """Download all images as 1280px thumbnails from Wikimedia Commons."""
    print(f"\n{'='*60}")
    print("  IMAGES (50 photos, 1280px thumbnails)")
    print(f"{'='*60}")

    results: list[dict] = []
    for i, img in enumerate(IMAGES, 1):
        category = img["category"]
        filename = img["filename"]
        commons_name = img["commons_name"]
        hash_path = img["hash_path"]

        # Build thumbnail URL: /thumb/{hash_path}/{name}/1280px-{name}
        # SVG files cannot be resized by the thumbnailer — use original
        if commons_name.endswith(".svg"):
            thumb_url = f"{WIKIMEDIA_BASE}/{hash_path}/{commons_name}"
        else:
            thumb_url = (
                f"{WIKIMEDIA_BASE}/thumb/{hash_path}/{commons_name}"
                f"/1280px-{commons_name}"
            )

        dest_dir = DATA_DIR / "images" / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename

        tag = f"[{i:2d}/50] {category}/{filename}"
        if dest.exists():
            print(f"  {tag} — skipped (exists, {dest.stat().st_size:,} bytes)")
            results.append({
                "filename": filename,
                "category": category,
                "description": img["description"],
                "source": "Wikimedia Commons",
                "local_path": str(dest.relative_to(DATA_DIR)),
                "size_bytes": dest.stat().st_size,
                "status": "cached",
            })
            continue

        print(f"  {tag} ...", end="", flush=True)
        ok = _download_url(thumb_url, dest, delay=REQUEST_DELAY)
        size = dest.stat().st_size if ok else 0
        status = "OK" if ok else "FAILED"
        print(f" {status} ({size:,} bytes)")

        results.append({
            "filename": filename,
            "category": category,
            "description": img["description"],
            "source": "Wikimedia Commons",
            "url": thumb_url,
            "local_path": str(dest.relative_to(DATA_DIR)),
            "size_bytes": size,
            "status": status,
        })

    succeeded = sum(1 for r in results if r["status"] in ("OK", "cached"))
    print(f"\n  Images: {succeeded}/50 downloaded")
    return results


def download_videos(small_only: bool = False) -> list[dict]:
    """Download video clips from Wikimedia Commons."""
    videos = VIDEOS
    if small_only:
        videos = [v for v in VIDEOS if v.get("small", False)]

    print(f"\n{'='*60}")
    print(f"  VIDEOS ({len(videos)} clips{' (small only)' if small_only else ''})")
    print(f"{'='*60}")

    dest_dir = DATA_DIR / "videos"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for i, vid in enumerate(videos, 1):
        filename = vid["filename"]
        url = vid["url"]
        dest = dest_dir / filename

        tag = f"[{i}/{len(videos)}] {filename}"
        if dest.exists():
            print(f"  {tag} — skipped (exists, {dest.stat().st_size:,} bytes)")
            results.append({
                "filename": filename,
                "description": vid["description"],
                "license": vid["license"],
                "source": "Wikimedia Commons",
                "local_path": str(dest.relative_to(DATA_DIR)),
                "size_bytes": dest.stat().st_size,
                "status": "cached",
            })
            continue

        print(f"  {tag} ...", end="", flush=True)
        ok = _download_url(url, dest)
        size = dest.stat().st_size if ok else 0
        status = "OK" if ok else "FAILED"
        print(f" {status} ({size:,} bytes)")

        results.append({
            "filename": filename,
            "description": vid["description"],
            "license": vid["license"],
            "source": "Wikimedia Commons",
            "url": url,
            "local_path": str(dest.relative_to(DATA_DIR)),
            "size_bytes": size,
            "status": status,
        })

    succeeded = sum(1 for r in results if r["status"] in ("OK", "cached"))
    print(f"\n  Videos: {succeeded}/{len(videos)} downloaded")
    return results


def download_texts() -> list[dict]:
    """Download public domain texts from Project Gutenberg."""
    print(f"\n{'='*60}")
    print("  TEXTS (10 books, Project Gutenberg)")
    print(f"{'='*60}")

    dest_dir = DATA_DIR / "texts"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for i, txt in enumerate(TEXTS, 1):
        gid = txt["gutenberg_id"]
        filename = txt["filename"]
        # Try -0.txt first (newer format), fallback to {id}.txt
        url = f"{GUTENBERG_BASE}/{gid}/{gid}-0.txt"
        url_fallback = f"{GUTENBERG_BASE}/{gid}/{gid}.txt"

        dest = dest_dir / filename

        tag = f"[{i:2d}/10] {filename}"
        if dest.exists():
            print(f"  {tag} — skipped (exists, {dest.stat().st_size:,} bytes)")
            results.append({
                "filename": filename,
                "title": txt["title"],
                "author": txt["author"],
                "language": txt["language"],
                "source": "Project Gutenberg",
                "gutenberg_id": int(gid),
                "local_path": str(dest.relative_to(DATA_DIR)),
                "size_bytes": dest.stat().st_size,
                "status": "cached",
            })
            continue

        print(f"  {tag} ...", end="", flush=True)
        ok = _download_url(url, dest)
        if not ok and dest.stat().st_size == 0:
            ok = _download_url(url_fallback, dest)

        size = dest.stat().st_size if ok else 0
        status = "OK" if ok else "FAILED"
        print(f" {status} ({size:,} bytes)")

        results.append({
            "filename": filename,
            "title": txt["title"],
            "author": txt["author"],
            "language": txt["language"],
            "source": "Project Gutenberg",
            "gutenberg_id": int(gid),
            "url": url,
            "local_path": str(dest.relative_to(DATA_DIR)),
            "size_bytes": size,
            "status": status,
        })

    succeeded = sum(1 for r in results if r["status"] in ("OK", "cached"))
    print(f"\n  Texts: {succeeded}/10 downloaded")
    return results


def download_audios() -> list[dict]:
    """Download diverse audio files from Wikimedia Commons."""
    print(f"\n{'='*60}")
    print(f"  AUDIOS ({len(AUDIOS)} files)")
    print(f"{'='*60}")

    dest_dir = DATA_DIR / "audio"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for i, aud in enumerate(AUDIOS, 1):
        category = aud["category"]
        filename = aud["filename"]
        url = aud["url"]

        sub_dir = dest_dir / category
        sub_dir.mkdir(parents=True, exist_ok=True)
        dest = sub_dir / filename

        tag = f"[{i:2d}/{len(AUDIOS)}] {category}/{filename}"
        if dest.exists():
            print(f"  {tag} — skipped (exists, {dest.stat().st_size:,} bytes)")
            results.append({
                "filename": filename,
                "category": category,
                "description": aud["description"],
                "license": aud["license"],
                "source": "Wikimedia Commons",
                "local_path": str(dest.relative_to(DATA_DIR)),
                "size_bytes": dest.stat().st_size,
                "status": "cached",
            })
            continue

        print(f"  {tag} ...", end="", flush=True)
        ok = _download_url(url, dest, delay=REQUEST_DELAY)
        size = dest.stat().st_size if ok else 0
        status = "OK" if ok else "FAILED"
        print(f" {status} ({size:,} bytes)")

        results.append({
            "filename": filename,
            "category": category,
            "description": aud["description"],
            "license": aud["license"],
            "source": "Wikimedia Commons",
            "url": url,
            "local_path": str(dest.relative_to(DATA_DIR)),
            "size_bytes": size,
            "status": status,
        })

    succeeded = sum(1 for r in results if r["status"] in ("OK", "cached"))
    print(f"\n  Audio: {succeeded}/{len(AUDIOS)} downloaded")
    return results


def generate_manifest(
    images: list[dict],
    videos: list[dict],
    texts: list[dict],
    audios: list[dict],
) -> None:
    """Write dataset_manifest.json with all metadata."""
    manifest = {
        "name": "arrow-lake-multimodal-tutorial",
        "version": "1.0",
        "description": "Multimodal sample data for Arrow Lake tutorial examples",
        "images": images,
        "videos": videos,
        "texts": texts,
        "audios": audios,
        "statistics": {
            "total_images": len(images),
            "total_videos": len(videos),
            "total_texts": len(texts),
            "total_audios": len(audios),
            "total_size_bytes": sum(
                r["size_bytes"] for r in images + videos + texts + audios
            ),
        },
    }

    dest = DATA_DIR / "dataset_manifest.json"
    dest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    total_mb = manifest["statistics"]["total_size_bytes"] / (1024 * 1024)
    print(f"\n  Manifest: {dest} ({total_mb:.1f} MB total)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download multimodal sample data for Arrow Lake examples.",
    )
    parser.add_argument(
        "--images-only",
        action="store_true",
        help="Download only images",
    )
    parser.add_argument(
        "--videos-only",
        action="store_true",
        help="Download only videos",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="Download only audio",
    )
    parser.add_argument(
        "--texts-only",
        action="store_true",
        help="Download only texts",
    )
    parser.add_argument(
        "--small",
        action="store_true",
        help="Skip large files (only small video clips)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Arrow Lake - Multimodal Data Downloader")
    print(f"  Target: {DATA_DIR}")
    print("=" * 60)

    download_all = not (args.images_only or args.videos_only or args.audio_only or args.texts_only)

    images: list[dict] = []
    videos: list[dict] = []
    audios: list[dict] = []
    texts: list[dict] = []

    if download_all or args.images_only:
        images = download_images()

    if download_all or args.videos_only:
        videos = download_videos(small_only=args.small)

    if download_all or args.audio_only:
        audios = download_audios()

    if download_all or args.texts_only:
        texts = download_texts()

    if images or videos or audios or texts:
        generate_manifest(images, videos, texts, audios)

        # Summary
        total_bytes = sum(r["size_bytes"] for r in images + videos + audios + texts)
        print(f"\n{'='*60}")
        print(f"  DONE: {len(images)} images + {len(videos)} videos + {len(audios)} audio + {len(texts)} texts")
        print(f"  Total: {total_bytes / (1024*1024):.1f} MB")
        print(f"{'='*60}")
    else:
        print("\n  Nothing to download. Use --images-only, --videos-only, --audio-only, or --texts-only.")


if __name__ == "__main__":
    main()
