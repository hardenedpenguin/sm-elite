# Themes

**Theme** in the header lists the built-in themes (dark, light, blue, green, seafoam, and neon purple) with a small preview of each. A pick is kept in that browser only, so a kiosk screen and a phone can each have their own.

**New custom theme** starts from the theme in use and opens a color picker for the page colors (background, panels, borders, text, buttons) and the status colors (local, network, telemetry, playback, connected, favorite activity, idle, warnings). The page changes as you pick, and a note appears when text would be hard to read. Custom themes have **Edit**, **Export**, and **Delete** buttons. Export saves a small `.json` file that anyone can bring into their own browser with **Import a theme file**.

Admins also see **Site default**: **Use my current theme for everyone** makes the theme in use the default for every browser that has not picked one, including people who are not logged in. **Go back to the stock dark theme** clears it. The site default is kept in `smelite.db`, so it is part of the users group in a [settings backup](backup.md).
