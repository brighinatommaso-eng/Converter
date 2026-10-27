# Release notes

One file per version: `v<version>.md` (for example `v0.1.4.md`). The build
workflow (`.github/workflows/build.yml`) uses it as the body of the
draft release it creates when the tag `v<version>` is pushed; a tag without its
notes file fails the build.

- An optional first line `# Title` becomes the release title (otherwise `v<version>`).
- Don't write the checksums: the workflow appends a "Verify the download" section
  with the SHA256 of every file (Windows zip and installer, Linux AppImage and .deb) (an existing section with that
  title is replaced).

Release steps:

1. Bump the version in `converter/__init__.py`, `pyproject.toml`,
   `version_info.txt` (and the mentions in `README.md` / `PROJECT_OVERVIEW.md`).
2. Write `docs/releases/v<version>.md`, merge to `main`.
3. Tag `main`: `git tag v<version> && git push origin v<version>`.
4. When the workflow is green, open the draft on the Releases page, check it and
   press **Publish release**.
