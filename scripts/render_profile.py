"""Render the Copper profile from authenticated aggregate GitHub statistics."""

import argparse
from dataclasses import dataclass
from datetime import date
from html import escape
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from .profile_metrics import overall_rank, trophy_rank, trophy_scores

ROOT = Path(__file__).resolve().parents[1]
NS = "{http://www.w3.org/2000/svg}"
ET.register_namespace("", NS[1:-1])
THEMES = {
    "dark": {"fg": "#f0f6fc", "muted": "#9da7b3", "line": "#30363d", "accent": "#e2a180"},
    "light": {"fg": "#1f2328", "muted": "#59636e", "line": "#d1d9e0", "accent": "#9c4b2a"},
}


class RenderError(Exception):
    pass


def texts(element: ET.Element) -> list[str]:
    return [" ".join("".join(e.itertext()).split()) for e in element.iter(NS + "text")]


@dataclass
class Trophy:
    rank: str
    name: str
    title: str
    points: str
    progress: float
    artwork: str


@dataclass
class Profile:
    streak: list[str]
    rank: str
    rank_progress: float
    stats: list[tuple[str, str]]
    languages: list[dict]
    trophies: list[Trophy]


def load_profile(assets: Path) -> Profile:
    snapshot = json.loads((assets / "profile.json").read_text(encoding="utf-8"))
    stats, streak_data = snapshot["stats"], snapshot["streak"]
    current = streak_data["current"]
    for value in [*stats.values(), snapshot["language_count"], streak_data["total"], current["weeks"]]:
        if type(value) is not int or value < 0:
            raise RenderError("The profile snapshot contains an invalid statistic count.")
    created, today = date.fromisoformat(snapshot["created"]), date.fromisoformat(snapshot["as_of"])
    if created > today:
        raise RenderError("The profile snapshot contains invalid dates.")

    def display_date(value):
        day = date.fromisoformat(value)
        return f"{day:%b} {day.day}, {day.year}"

    def period(value):
        if not value["weeks"]:
            return "No active streak"
        start, end = date.fromisoformat(value["start"]), date.fromisoformat(value["end"])
        if not created <= start <= end <= today:
            # A Sunday-start week may begin before the account's creation date.
            if not created.toordinal() - 6 <= start.toordinal() <= end.toordinal() <= today.toordinal():
                raise RenderError("The profile snapshot contains an invalid streak period.")
        return f"{display_date(value['start'])} - {display_date(value['end'])}"

    streak = [f"{streak_data['total']:,}", "Total contributions", display_date(snapshot["created"]) + " - Present",
              "Current week streak", period(current), str(current["weeks"])]
    rows = [(label, f"{stats[key]:,}") for key, label in
            (("stars", "Total Stars Earned"), ("commits", "Total Commits"), ("prs", "Total PRs"),
             ("issues", "Total Issues"), ("contributed_to", "Contributed to (last year)"))]
    rank, rank_progress = overall_rank(stats)
    trophy_data = json.loads((assets / "trophy-art.json").read_text(encoding="utf-8"))
    trophies = []
    for index, (name, score) in enumerate(trophy_scores(snapshot).items()):
        trophy_level, title, progress = trophy_rank(score, trophy_data["rules"][name])
        if trophy_level == "?":
            continue
        artwork = list(ET.fromstring(f'<svg xmlns="{NS[1:-1]}">{trophy_data["icons"][trophy_level]}</svg>'))
        # Several source cards reuse gradient IDs. Give each trophy its own IDs.
        ids = {e.attrib["id"]: f"trophy-{index}-{e.attrib['id']}" for child in artwork for e in child.iter() if "id" in e.attrib}
        for child in artwork:
            for e in child.iter():
                for key, value in list(e.attrib.items()):
                    if key == "id":
                        e.set(key, ids[value])
                    else:
                        for old, new in ids.items():
                            value = value.replace(f"url(#{old})", f"url(#{new})")
                        e.set(key, value)
        points = f"{score / 1000:.1f}kpt" if score > 999 else f"{score}pt"
        trophies.append(Trophy(trophy_level, name, title, points, progress, "".join(ET.tostring(child, encoding="unicode") for child in artwork)))
    languages = snapshot["languages"]
    names = set()
    for language in languages:
        if not isinstance(language["name"], str) or not language["name"] or language["name"] in names:
            raise RenderError("Language names must be nonempty and unique.")
        names.add(language["name"])
        if type(language["percentage"]) not in (int, float) or not 0 <= language["percentage"] <= 100 or not re.fullmatch(r"#[0-9a-fA-F]{6}", language["color"]):
            raise RenderError("Language shares contain an invalid percentage or color.")
    if not languages:
        raise RenderError("No cached language totals were found.")
    if len(languages) > 8 or abs(sum(language["percentage"] for language in languages) - 100) > 0.0001:
        raise RenderError("The language snapshot must contain up to eight shares totaling 100 percent.")
    languages = sorted(languages, key=lambda language: (-language["percentage"], language["name"]))
    return Profile(streak, rank, rank_progress, rows, languages, trophies)


class Svg:
    def __init__(self, theme: str):
        self.colors = THEMES[theme]

    def text(self, x, y, value, size=14, color="fg", weight=400, anchor="start", mono=False) -> str:
        font = "Consolas,monospace" if mono else "-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif"
        return f'<text x="{x}" y="{y}" fill="{self.colors[color]}" font-family="{font}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>'

    def line(self, x1, y1, x2, y2) -> str:
        return f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{self.colors["line"]}"/>'

    def box(self, width, height) -> str:
        return f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="8" fill="none" stroke="{self.colors["line"]}"/>'

    def ring(self, x, y, radius, progress, thickness=3) -> str:
        circumference = 2 * math.pi * radius
        return f'<circle cx="{x}" cy="{y}" r="{radius}" fill="none" stroke="{self.colors["line"]}" stroke-width="{thickness}"/><circle cx="{x}" cy="{y}" r="{radius}" fill="none" stroke="{self.colors["accent"]}" stroke-width="{thickness}" stroke-linecap="round" stroke-dasharray="{circumference * progress:.3f} {circumference:.3f}" transform="rotate(-90 {x} {y})"/>'


def place(x, y, content, scale=1) -> str:
    return f'<g transform="translate({x} {y}) scale({scale})">{content}</g>'


def streak_card(s: Svg, profile: Profile, mobile: bool) -> str:
    v = profile.streak
    if mobile:
        body = s.ring(61, 72, 46, 1, 2) + s.text(61, 81, v[5], 36, "accent", 500, "middle")
        body += s.text(61, 99, "WEEKS", 9, "muted", 500, "middle")
        body += s.text(126, 67, "Current week streak", 13, weight=600) + s.text(126, 90, v[4], 9, "muted")
        body += s.line(0, 146, 330, 146)
        body += s.text(165, 177, "TOTAL CONTRIBUTIONS", 11, "muted", 600, "middle")
        body += s.text(165, 222, v[0], 43, weight=500, anchor="middle") + s.text(165, 250, v[2], 11, "muted", anchor="middle")
        return body + s.line(0, 269, 330, 269)
    body = s.ring(85, 76, 52, 1, 2) + s.text(85, 84, v[5], 38, "accent", 500, "middle")
    body += s.text(85, 102, "WEEKS", 9, "muted", 500, "middle")
    body += s.text(166, 69, "Current week streak", 15, weight=600) + s.text(166, 94, v[4], 11, "muted")
    body += s.line(416, 26, 416, 129)
    body += s.text(456, 45, "TOTAL CONTRIBUTIONS", 11, "muted", 600)
    body += s.text(456, 96, v[0], 45, weight=500) + s.text(456, 124, v[2], 11, "muted")
    return body + s.line(0, 156, 780, 156)


def stats_card(s: Svg, profile: Profile, mobile: bool) -> str:
    body = s.box(330 if mobile else 440, 238) + s.text(22, 34, "GitHub stats", 16, weight=600)
    for i, (label, value) in enumerate(profile.stats):
        y = 75 + i * 31
        body += s.text(22, y, label.rstrip(":"), 13, "muted")
        body += s.text(310 if mobile else 276, y, value, 14, weight=600, anchor="end", mono=True)
    if mobile:
        body += s.text(310, 34, "Rank " + profile.rank, 13, "accent", 600, "end")
    else:
        body += s.ring(363, 124, 39, profile.rank_progress, 5)
        body += s.text(363, 134, profile.rank, 27, weight=600, anchor="middle")
        body += s.text(363, 188, "OVERALL RANK", 9, "muted", 500, "middle")
    return body


def language_card(s: Svg, profile: Profile) -> str:
    body = s.box(330, 238) + s.text(22, 34, "Most used languages", 16, weight=600)
    x = 22.0
    for language in profile.languages:
        width = language["percentage"] / 100 * 286
        body += f'<rect x="{x:.4f}" y="56" width="{width:.4f}" height="7" fill="{language["color"]}"/>'
        x += width
    for i, language in enumerate(profile.languages):
        column, row = divmod(i, 4)
        x, y = 22 + column * 148, 96 + row * 32
        body += f'<circle cx="{x+3}" cy="{y-4}" r="3" fill="{language["color"]}"/>'
        body += s.text(x + 13, y, language["name"], 10.5, "muted")
        body += s.text(x + 13, y + 13, f'{language["percentage"]:.2f}%', 11, weight=500, mono=True)
    return body


def trophy_card(s: Svg, trophy: Trophy) -> str:
    body = f'<svg x="0" y="0" width="78" height="72" viewBox="0 10 110 90">{trophy.artwork}</svg>'
    body += s.text(82, 27, trophy.name, 10.5, weight=600) + s.text(82, 47, trophy.points, 11, "muted", mono=True)
    body += s.text(10, 84, trophy.title, 10.4, "muted") + s.line(10, 101, 170, 101)
    body += f'<path d="M10 101h{160 * trophy.progress:.3f}" stroke="{s.colors["accent"]}" stroke-width="2"/>'
    return body


def render(profile: Profile, theme: str, mobile=False) -> str:
    s = Svg(theme)
    width = 330 if mobile else 780
    body = streak_card(s, profile, mobile)
    body += place(0, 287 if mobile else 176, stats_card(s, profile, mobile))
    body += place(0 if mobile else 450, 541 if mobile else 176, language_card(s, profile))
    trophy_y = 826 if mobile else 460
    body += s.text(0, trophy_y - 18, "Trophies", 14, weight=600)
    ranked = [t for t in profile.trophies if t.rank != "?"]
    columns, step_x, step_y, scale = (2, 174, 110, 156 / 180) if mobile else (4, 200, 116, 1)
    for i, trophy in enumerate(ranked):
        row, column = divmod(i, columns)
        body += place(column * step_x, trophy_y + row * step_y, trophy_card(s, trophy), scale)
    height = trophy_y + math.ceil(len(ranked) / columns) * step_y
    description = f"Current week streak: {profile.streak[5]}. {profile.streak[4]}. Total contributions: {profile.streak[0]}. "
    description += ". ".join(f"{label} {value}" for label, value in profile.stats) + f". Rank {profile.rank}. "
    description += "Statistics include public and private activity accessible to the profile owner's token. Language percentages use code bytes in the eight largest languages across owned, non-forked repositories."
    result = f'<svg xmlns="{NS[1:-1]}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description"><title id="title">GitHub statistics and trophies</title><desc id="description">{escape(description)}</desc>{body}</svg>\n'
    result = "\n".join(line.rstrip() for line in result.splitlines()) + "\n"
    ET.fromstring(result)
    return result


def render_files(assets: Path, output: Path) -> None:
    profile = load_profile(assets)
    rendered = {f"profile{'-mobile' if mobile else ''}-{theme}.svg": render(profile, theme, mobile) for theme in THEMES for mobile in (False, True)}
    output.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        temporary = output / (name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(output / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ROOT / "assets")
    parser.add_argument("--output", type=Path, default=ROOT / "assets")
    args = parser.parse_args()
    try:
        render_files(args.assets, args.output)
    except (RenderError, OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
        print(f"Profile rendering failed: {error}", file=sys.stderr)
        return 1
    print("Rendered Copper profile for dark, light, and mobile layouts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
