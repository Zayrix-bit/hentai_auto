import json
import sys
from collections import defaultdict

# Force UTF-8 encoding for standard output
sys.stdout.reconfigure(encoding='utf-8')

with open('data/videos.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Comprehensive anime series registry with known full episode counts on MyAnimeList / databases
KNOWN_FULL_EPISODES = {
    "Secret Mission": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes total (Sennyuu Sousakan wa Zettai ni Makenai!)"},
    "My Classmate's a Sexy Actress, and Now We Live Together": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes total (Onaji Zemi no Someya-san...)"},
    "Does it Count if You Lose Your Innocence to an Android": {"total": 9, "type": "TV + OVA", "notes": "8 regular episodes + 1 OVA (Android wa Keiken Ninzuu...)"},
    "What She Fell on Was the Tip of My Dick": {"total": 9, "type": "TV/Shorts", "notes": "9 episodes total (Joshiochi! 2-kai kara Onnanoko ga...)"},
    "Overflow": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes total"},
    "Harem Camp": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes total"},
    "The Bird in a Shell": {"total": 4, "type": "OVA", "notes": "4 character OVA parts complete"},
    "Shin Ruri-iro no Yuki": {"total": 4, "type": "OVA", "notes": "4 OVA parts complete"},
    "Nightmare Campus": {"total": 5, "type": "OVA", "notes": "5 OVA parts complete (Episodes 1-5)"},
    "Pigeon Blood": {"total": 2, "type": "OVA", "notes": "2 training parts complete"},
    "Wet Nurse": {"total": 2, "type": "OVA", "notes": "2 parts complete"},
    "Spotlight": {"total": 2, "type": "OVA", "notes": "2 parts complete"},
    "Stepmother and Stepsister": {"total": 2, "type": "OVA", "notes": "2 parts complete"},
    "Now That I Can Control Reality With A Mouse Cursor, I'm Gonna Click Away On The Girls": {"total": 1, "type": "Currently Airing (Fall 2026)", "notes": "Episode 1 released"},
    "The Lonely Snow Widow and the Cursed Ring": {"total": 1, "type": "Currently Airing (Fall 2026)", "notes": "Episode 1 released"},
    "Fuzzy Lips": {"total": 2, "type": "OVA", "notes": "Older OVA series"},
    "Sex on the Train with Horny Sluts": {"total": 2, "type": "OVA", "notes": "Episode 2 uploaded"},
    "Bijukubo": {"total": 2, "type": "OVA", "notes": "Part 2 uploaded"},
    "Booby Life": {"total": 1, "type": "OVA", "notes": "Single 60-min OVA volume complete (Oppai Life)"},
    "Muchuu no Tou": {"total": 2, "type": "Currently Airing (Summer/Fall 2026)", "notes": "Episodes 1 & 2 released"},
    "KAMUI": {"total": 1, "type": "TV/Shorts", "notes": "Episode 1 Dub released (Ushiro no Shoumen Kamui-san)"},
    "Ateuma Chara no Kuse Shite, Supadari Ouji ni Chouai Sarete Imasu": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "Papa Datte, Shitai": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "Kuro Gal ni Natta kara Shin`yuu to Shite Mita": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "Maou Evelogia ni Mi o Sasageyo": {"total": 9, "type": "TV/Shorts", "notes": "9 episodes complete"},
    "Mori no Kuma-san, Toumin-chuu": {"total": 9, "type": "TV/Shorts", "notes": "9 episodes complete"},
    "Reika wa Karei na Boku no Joou": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Yatara Yarashii Fukami-kun": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "Dokidoki Little Ooyasan": {"total": 6, "type": "OVA", "notes": "6 OVA episodes complete"},
    "Enjo Kouhai": {"total": 10, "type": "OVA", "notes": "10 OVA episodes complete"},
    "Sister Breeder": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Boku no Risou no Isekai Seikatsu": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Ane Chijo Max Heart": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Baka na Imouto o Rikou ni Suru no wa Ore no XX Dake na Ken ni Tsuite": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Ookii Onnanoko wa Suki desu ka?": {"total": 12, "type": "TV/Shorts", "notes": "12 episodes complete"},
    "JK to Ero Konbini Tenchou": {"total": 6, "type": "OVA", "notes": "6 OVA episodes complete"},
    "Isekai Harem Monogatari": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Kegareboshi": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Hajimete no Hitozuma": {"total": 5, "type": "OVA", "notes": "5 OVA episodes complete"},
    "Ichigo Aika: Zatsu de Namaiki na Imouto to Warikirenai Ani": {"total": 12, "type": "TV/Shorts", "notes": "12 episodes complete"},
    "Arisugawa Ren tte Honto wa Onna Nanda yo ne": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "Imaizumin-chi wa Douyara Gal no Tamariba ni Natteru Rashii": {"total": 6, "type": "OVA", "notes": "6 OVA episodes complete"},
    "Isekai Kita no de Sukebe Skill de Zenryoku Ouka Shiyou to Omou": {"total": 7, "type": "OVA", "notes": "7 episodes complete"},
    "Tsundero Series": {"total": 6, "type": "OVA", "notes": "6 episodes complete"},
    "Eroriman": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Hime-sama Love Life!": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Iribitari Gal ni Manko Tsukawasete Morau Hanashi": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Dousou Kai: Yesterday Once More": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Houkago: Nureta Seifuku": {"total": 3, "type": "OVA", "notes": "3 OVA episodes complete"},
    "Nagachichi Nagai-san": {"total": 3, "type": "OVA", "notes": "3 OVA episodes complete"},
    "Akiba-kei Kanojo": {"total": 3, "type": "OVA", "notes": "3 OVA episodes complete"},
    "Guilty Hole": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "1LDK+JK Ikinari Doukyo? Micchaku!? Hatsu Ecchi!!?": {"total": 6, "type": "OVA", "notes": "6 OVA episodes complete"},
    "Modaete yo, Adam-kun": {"total": 8, "type": "TV/Shorts", "notes": "8 episodes complete"},
    "Tiny Evil": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Shakuen no Eris": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "White Blue": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Nozoki Kanojo": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Oni Chichi: Refresh": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Sansha Mendan: Rensa Suru Chijoku Choukyou no Gakuen": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Mako-chan Kaihatsu Nikki": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Papa Katsu!": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Saimin Seishidou": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Kimi wa Yasashiku Netorareru The Animation": {"total": 4, "type": "OVA", "notes": "4 OVA episodes complete"},
    "Tsuma ni Damatte Sokubaikai ni Ikun ja Nakatta": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Yari Agari": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Oshikake! Bakunyuu Gyaru Harem Seikatsu": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Netorareta Bakunyuu Tsuma-tachi": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Natsu Zuma": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Ran-Sem: Hakudaku Delmo Tsuma no Miira Tori": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Rin x Sen: Hakudaku Onna Kyoushi to Yaroudomo": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Otogibanashi no Onigokko": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"},
    "Oni Chichi: Re-born": {"total": 2, "type": "OVA", "notes": "2 OVA episodes complete"}
}

import re

def get_base_series(title):
    tl = title.lower()
    if "someya-san" in tl or "sexy actress" in tl:
        return "My Classmate's a Sexy Actress, and Now We Live Together"
    if "harem camp" in tl:
        return "Harem Camp"
    if "furueru kuchibiru" in tl or "fuzzy lips" in tl:
        return "Fuzzy Lips"
    if "jashin shoukan" in tl or "sex on the train" in tl:
        return "Sex on the Train with Horny Sluts"
    if "bijukubo" in tl:
        return "Bijukubo"
    if "nightmare campus" in tl:
        return "Nightmare Campus"
    
    # Check mappings
    for base in KNOWN_FULL_EPISODES:
        if base.lower() in tl:
            return base
    return re.sub(r'\s+\d+$', '', title).strip()

def extract_episode_or_part(title, existing_ep):
    tl = title.lower()
    # Check for "Part One", "Part Two", etc.
    word_map = {"one": "01", "two": "02", "three": "03", "four": "04", "first": "01", "second": "02"}
    for w, num in word_map.items():
        if f"part {w}" in tl or f"{w} training" in tl:
            return num
    
    # Check for Nightmare Campus episodes (e.g. "Nightmare Campus 4", "Nightmare Campus ... ep1", "ep5")
    m = re.search(r'(?:nightmare campus\s+(\d+)|(?:nightmare campus|gedou gakuen).*?(?:ep|#)\s*(\d+))', tl)
    if m:
        val = m.group(1) or m.group(2)
        return f"{int(val):02d}"

    # Check for Sex on the Train #02
    m = re.search(r'#(\d+)', tl)
    if m:
        return f"{int(m.group(1)):02d}"

    # Check for "The Bird in a Shell 1", "Stepmother and Stepsister 2"
    m = re.search(r'(?:the bird in a shell|stepmother and stepsister)\s+(\d+)', tl)
    if m:
        return f"{int(m.group(1)):02d}"
        
    m_trailing = re.search(r'\s+(\d+)$', title)
    if m_trailing and (not existing_ep or existing_ep == "01"):
        return f"{int(m_trailing.group(1)):02d}"

    return existing_ep or "01"

series_map = defaultdict(list)
for v in data:
    title = v.get("title", "")
    base = v.get("series") or get_base_series(title)
    ep = extract_episode_or_part(title, v.get("episode", ""))
    part = v.get("part", "")
    series_map[base].append({
        "title": title,
        "ep": ep,
        "part": part,
        "mal_id": v.get("mal_id"),
        "source_id": v.get("source_id")
    })

print(f"=== HENTAI SERIES & EPISODE COMPLETION REPORT ===")
print(f"Total videos in catalog: {len(data)}")
print(f"Total series identified: {len(series_map)}\n")

complete_series = []
incomplete_series = []
airing_series = []

for sname, items in sorted(series_map.items()):
    eps_set = {x["ep"] for x in items if x["ep"]}
    eps_sorted = sorted(list(eps_set), key=lambda x: int(x) if x.isdigit() else 999)
    known = KNOWN_FULL_EPISODES.get(sname, {})
    total_expected = known.get("total", "Unknown")
    
    # Check completion
    is_complete = False
    if isinstance(total_expected, int):
        if len(eps_sorted) >= total_expected:
            is_complete = True
    
    entry = {
        "name": sname,
        "uploaded_eps": eps_sorted,
        "count": len(eps_sorted),
        "total_expected": total_expected,
        "notes": known.get("notes", ""),
        "type": known.get("type", "OVA/Series"),
        "total_files": len(items)
    }
    
    if "Currently Airing" in str(known.get("type", "")):
        airing_series.append(entry)
    elif is_complete:
        complete_series.append(entry)
    else:
        incomplete_series.append(entry)

print(f"🟢 POORE (100% Complete Series): {len(complete_series)}")
for s in complete_series:
    print(f"  * {s['name']}: All {s['count']}/{s['total_expected']} Episodes Uploaded {s['uploaded_eps']} ({s['total_files']} files, {s['notes']})")

print(f"\n🟡 INCOMPLETE / ADHOORE (Doomdos ne saare episode upload nahi kiye the): {len(incomplete_series)}")
for s in incomplete_series:
    print(f"  * {s['name']}: {s['count']}/{s['total_expected']} Episodes Uploaded {s['uploaded_eps']} -> Missing: {[f'{i:02d}' for i in range(1, s['total_expected']+1) if f'{i:02d}' not in s['uploaded_eps']] if isinstance(s['total_expected'], int) else 'Unknown'} ({s['notes']})")

print(f"\n🔵 ONGOING / NEW RELEASES (Abhi sirf 1st Episode hi release hua hai): {len(airing_series)}")
for s in airing_series:
    print(f"  * {s['name']}: Episode {s['uploaded_eps']} Uploaded (Abhi latest release hai: {s['notes']})")
