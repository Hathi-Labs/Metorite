# What to look for

The defect classes this repo produces, with the rule each one breaks. Work down
the list against your captures. Each entry names the context that reveals it,
because most of them are invisible in the one context you develop in.

## 1. A semantic colour wired to `--primary`

**Context: accent.** `--primary` is the accent a member picks at Settings →
Appearance. It means "selected". Anything else that reads it inherits a meaning
it must not have.

Compare the accent capture against the dark one. Any element that changed
colour and is **not** a selection is a defect. A status lane, a severity chip
and a category swatch must all hold still.

The tell in code is a hue whose value is `bg-primary` or `text-primary` inside a
map of semantic colours.

## 2. A pinned `px` font size

**Context: compact and comfortable.** `globals.css` sets the root font size from
`--ui-scale`. A `rem` follows the member's density. A `text-[11px]` does not.

Put the compact capture beside the default one. Look for two sizes that were
different and are now nearly equal. A card title and its meta row is the usual
pair. The hierarchy compresses where a dense view needs it most.

## 3. A native control

**Context: light.** `src/components/ui/` holds the house primitives. A raw
`<input type="checkbox">` or `<select>` takes its paint from the platform. It
recedes in dark mode, and it turns into a heavy black block in light mode.

Scan the light capture for the heaviest marks on the screen. If a checkbox
outweighs the text beside it, that is this.

## 4. A row that wraps

**Context: 1440.** A control row that fits at 1920 and wraps at 1440 costs a
band of vertical space on the width most people use. Count the rows of chrome
above the content in the 1440 capture, and compare with 1920.

## 5. A default that announces itself

**Any context.** A field that prints its default value on every row carries no
information and crowds the row that has to hold the real signal. "Normal"
priority on every card is the example this repo produced.

Ask of every repeated chip: does this vary? If it does not, delete it.

## 6. An empty state that contradicts the page

**Any context.** An empty state is a claim about the world. A panel that says
"nothing here" beside a rail listing six things makes a false claim. It sends
the reader to create what they already have.

Check that "no data" and "the request failed" produce different words.

## 7. A stat tile with no number

**Any context.** A tile exists to show one figure. Showing nothing reads as a
number that failed, and not as a number that is zero. Write zero as "0". Write
an unavailable figure as an explicit dash, with a reason.

## 8. A layout that stops

**Any context, and mobile most of all.** Content sized to itself leaves the rest
of the window empty while something else is clipped off the edge. Look for a
surface that runs out of room in one direction and wastes it in the other.

On a grid, look for an orphan — a last row holding one item. Either fill the row
or pick a column count the items divide into.

## 9. A blank pane

**Any context.** A component that reads a response field behind only a `!data`
guard throws when the field is absent. The layout boundary catches it and
renders nothing, so a failure looks like emptiness.

If a pane is blank, open the console before you conclude the data is empty.
Three components in this repo have done exactly this.

## 10. Tracked-out capitals and repeated labels

**Any context.** An ALL-CAPS eyebrow above a heading. A label that repeats the
title one line below it. A meta string joined by middle dots. An arrow appended
to a button. The `frontend-design` skill lists these as the commonest tells of
generated design. They are as wrong in a product as on a landing page.

## 11. A request built from `undefined`

**Read `notes.txt`, not the picture.** The harness writes every path the surface
asked for. A path that contains `undefined` means a fetch fired before its id
existed. It costs one round trip per load, and nobody sees it.

Also read the error list in the same file. A console error that never reaches
the screen is still a defect.

## Two questions to finish on

**Does it hold at the neighbour?** CLAUDE.md asks for continuity across apps.
Capture the surface beside the one a member reaches it from, and check that the
two read as one product.

**What did you not look at?** A capture is one frame of one state. Say plainly
which states you did not open — hover, focus, empty, error, loading, and long
content. An audit that hides its own scope is worse than a short one.
