# Gallery and research: done

- `index.html` is the page that the presenter opens first. It has a dawn hero with one line, the arc of seven steps, and six cards. It also has "What we learned" and the palette strip.
- Each card links to `./<version>/index.html`. It shows `./<version>/shots/desktop.png` when that file exists. An `onerror` handler removes a missing image, and a numbered sage tile shows in its place.
- `RESEARCH.md` covers 11 products, with a source link for each claim. `[I]` marks an inference.
- The page uses only `shared/tokens.css`. The files are `gallery/gallery.css` and `gallery/gallery.js`. The captures are `gallery/shots/desktop.png` (1440 by 900) and `gallery/shots/mobile.png` (390 by 844). Neither size scrolls sideways.

Weak: the cards show their tiles until each lane saves its `shots/desktop.png`. To refresh, reload the page. No rebuild is needed.
The research links come from search results. I did not open each page, so check a claim at its link before you quote it to a customer.
