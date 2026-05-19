# Slides Workflow Guide

A reusable system for creating browser-based presentations from Markdown content.

---

## Quick Start

### 1. Copy the Template

```powershell
Copy-Item templates/slideshow-template.html templates/my-presentation.html
```

### 2. Edit the HTML

Open the new file and:
1. Update `<title>` with your presentation name
2. Replace the example slides with your content
3. Customize colors if desired (see Theming below)

### 3. Serve and Present

```powershell
python run.py
```

Navigate to `http://localhost:8080/my-presentation.html`

### 4. Export to PDF

Click the **📄 Export PDF** button (top-left), then:
- Select "Save as PDF" as the destination
- Use **Landscape** orientation
- Set margins to **Minimum** or **None**
- Click Save

---

## Slide Structure

### Basic Slide

```html
<div class="slide">
    <h2>Slide Title</h2>
    <p>Your content goes here.</p>
</div>
```

### Title Slide

```html
<div class="slide title-slide">
    <h1>Presentation Title</h1>
    <p class="subtitle">Subtitle or tagline</p>
    <p class="subtitle" style="margin-top: 2rem;">Your Name</p>
</div>
```

### Closing Slide

```html
<div class="slide closing-slide">
    <h1>Thank You</h1>
    <p style="font-size: 1.5rem;">Questions?</p>
</div>
```

---

## Formatting Elements

### Text Emphasis

| Element | Usage | Result |
|---------|-------|--------|
| `<strong>text</strong>` | Key terms | Accent color, bold |
| `<em>text</em>` | Emphasis | Purple, italic |
| `<span class="highlight">text</span>` | Highlight | Yellow background |

### Callout Box

```html
<div class="note-box">
    <p><strong>Key Point:</strong> Important information here.</p>
</div>
```

### Bullet Lists

```html
<ul>
    <li>First point</li>
    <li>Second point</li>
    <li>Third point</li>
</ul>
```

---

## Navigation

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `→` or `Space` | Next slide |
| `←` | Previous slide |
| `Home` | First slide |
| `End` | Last slide |

### Touch/Mobile

- Swipe left → Next slide
- Swipe right → Previous slide

### Controls

- Previous/Next buttons at bottom
- Progress bar at top
- Slide counter (e.g., "3 / 14")

---

## Theming

### Change Colors

Edit the CSS variables at the top of the `<style>` section:

```css
:root {
    --accent-1: #667eea;  /* Primary color (purple-blue) */
    --accent-2: #764ba2;  /* Secondary color (purple) */
    --text-dark: #1a202c;
    --text-medium: #2d3748;
    --text-light: #4a5568;
}
```

### Theme Examples

**Blue Theme:**
```css
--accent-1: #3b82f6;
--accent-2: #1d4ed8;
```

**Green Theme:**
```css
--accent-1: #10b981;
--accent-2: #047857;
```

**Red Theme:**
```css
--accent-1: #ef4444;
--accent-2: #b91c1c;
```

**Orange Theme:**
```css
--accent-1: #f97316;
--accent-2: #c2410c;
```

---

## Converting Markdown to Slides

### Workflow

1. Write your content in Markdown
2. Use AI (Cascade, GPT, etc.) to convert to slide HTML
3. Paste slides into the template

### Prompt Template

```
Convert this Markdown document into HTML slides for my slideshow template.

Rules:
- Each ## heading becomes a new slide
- Use <div class="slide"> for each slide
- First slide should be title-slide with class="slide active title-slide"
- Use <strong> for key terms
- Use <span class="highlight"> for important phrases
- Use <div class="note-box"> for callouts
- Keep slides concise (3-5 bullet points max)
- Split long sections into multiple slides

Markdown:
[paste your content here]
```

### Example Conversion

**Markdown:**
```markdown
## The Problem

The current system is **slow and inefficient**. Users report:
- Long wait times
- Frequent errors
- Poor feedback
```

**HTML Output:**
```html
<div class="slide">
    <h2>The Problem</h2>
    <p>The current system is <strong>slow and inefficient</strong>. Users report:</p>
    <ul>
        <li>Long wait times</li>
        <li>Frequent errors</li>
        <li>Poor feedback</li>
    </ul>
</div>
```

---

## PDF Export Tips

### Best Settings

When using "Save as PDF" in Chrome/Edge:

| Setting | Recommended |
|---------|-------------|
| Layout | Landscape |
| Margins | None or Minimum |
| Background graphics | ✅ Enabled |
| Scale | Default (100%) |

### Troubleshooting

**Slides cut off:**
- Reduce content per slide
- Check padding in print styles

**Colors missing:**
- Enable "Background graphics" in print dialog

**Page breaks wrong:**
- Each slide has `page-break-after: always`
- Check for extra content outside slide divs

---

## Adding to run.py

To serve additional presentations, add routes in `run.py`:

```python
def do_GET(self):
    if self.path == "/" or self.path == "":
        self.path = "/dashboard.html"
    elif self.path == "/slides" or self.path == "/slides/":
        self.path = "/slides.html"
    elif self.path == "/my-presentation":
        self.path = "/my-presentation.html"
    return super().do_GET()
```

Or access directly via filename:
`http://localhost:8080/my-presentation.html`

---

## File Structure

```
your-project/
├── run.py                          # Server
├── templates/
│   ├── slideshow-template.html     # Reusable template
│   ├── slides.html                 # Your presentation
│   └── [other-presentations].html  # Additional decks
└── docs/
    └── SLIDES_WORKFLOW.md          # This guide
```

---

## Reusing Across Projects

### Option A: Copy Files

Copy these files to any project:
1. `templates/slideshow-template.html`
2. `SLIDES_WORKFLOW.md`
3. `run.py` (or integrate the server logic)

### Option B: Create a Shared Package

For frequent use, consider creating a standalone slides package:

```
owen-slides/
├── run.py
├── templates/
│   └── slideshow-template.html
├── presentations/
│   └── [your decks here]
└── README.md
```

### Option C: Constellation Integration

Add to any Constellation-tracked project:
1. Create `templates/` directory
2. Copy template file
3. Update run.py to serve templates
4. Presentations become part of project dashboard

---

## Accessibility Notes

The slideshow includes:
- Keyboard navigation (arrow keys, space)
- Touch/swipe support
- High contrast text
- Semantic HTML structure
- Responsive sizing for different screens

For additional accessibility:
- Use descriptive slide titles
- Keep text concise
- Ensure sufficient color contrast
- Test with screen readers if needed

---

## Examples

See `templates/slides.html` for a complete example presentation covering:
- Title slide with author
- Content slides with various formatting
- Callout boxes
- Bullet lists
- Closing slide
