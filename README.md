# Report Layout Mapper Skill

Codex-compatible skill for reproducing Odoo QWeb report layouts from PDF, scan, or camera-photo templates.

It helps agents:

- crop and deskew document photos into scanner-like pages;
- generate grid overlays and normalized/mm coordinates;
- classify static text, dynamic values, static assets, and line-item regions;
- suggest Odoo field mappings;
- generate QWeb positioning guidance;
- compare rendered report PDFs against the rectified template.

## Install

Install for Codex:

```bash
npx skills add github.com/trisetiohidayat/report-layout-mapper --skill report-layout-mapper --agent codex -y
```

Install for another supported agent by changing `--agent`, or omit `--agent` to choose interactively.

## Python Dependencies

The skill includes Python helper scripts. Install their dependencies in the Python environment used by your agent:

```bash
python3 -m pip install -r skills/report-layout-mapper/requirements.txt
```

Tesseract OCR is optional. Layout/box detection works without OCR.

## Layout

```text
skills/
  report-layout-mapper/
    SKILL.md
    agents/openai.yaml
    references/
    scripts/
    requirements.txt
```
