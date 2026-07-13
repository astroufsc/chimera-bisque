# Bisque

Chimera plugin for Software Bisque TheSky/TheSkyX telescopes and CCDSoft cameras

This is a plugin for the [Chimera observatory control system](https://github.com/astroufsc/chimera).

## Installation

```bash
pip install -U chimera_bisque
```

Or install from source:

```bash
pip install -U git+https://github.com/astroufsc/chimera-bisque.git
```

## Configuration Example

Add the following to your `chimera.config` file:


```yaml
instruments:
    # TheSkyX over TCP/IP (recommended; works from any OS running chimera)
    - name: telescope
      type: TheSkyXTelescope
      skyx_host: localhost
      skyx_port: 3040

    # TheSky 5/6 via Windows COM (requires Windows + the `windows` extra)
    - name: telescope
      type: TheSkyTelescope
      thesky: 6

    # CCDSoft camera via Windows COM (requires Windows + the `windows` extra)
    - name: camera
      type: CCDSoftCamera
```

The plugin ships two connection modes:

- **`TheSkyXTelescope`** talks to TheSkyX over its TCP/IP JavaScript scripting
  interface. It is pure Python and works on any platform.
- **`TheSkyTelescope`** and **`CCDSoftCamera`** drive TheSky 5/6 and CCDSoft
  through Windows COM automation. These only work on Windows with the optional
  `windows` extra installed (`uv sync --extra windows`, which pulls in
  `pywin32`). The plugin still installs and imports on non-Windows machines;
  the COM drivers simply cannot connect there.




## Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/astroufsc/chimera-bisque.git
cd chimera-bisque

# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install --install-hooks
```

### Running Tests

```bash
uv run pytest
```

### Code Quality

This project uses:
- [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- [pre-commit](https://pre-commit.com/) for automated checks

```bash
# Run linter
uv run ruff check

# Run formatter
uv run ruff format

# Run all pre-commit hooks
uv run pre-commit run --all-files
```

## License

GPL-2.0-or-later

## Contact

For more information, contact us on chimera's discussion list:
https://groups.google.com/forum/#!forum/chimera-discuss

Bug reports and patches are welcome and can be sent over our GitHub page:
https://github.com/astroufsc/chimera-bisque
