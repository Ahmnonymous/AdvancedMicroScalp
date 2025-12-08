# MetaTrader5 Installation Troubleshooting

## Common Error: "Could not find a version that satisfies the requirement MetaTrader5"

### Root Cause
MetaTrader5 Python package **only supports Python 3.6-3.10**. If you're using Python 3.11 or newer, pip cannot find any compatible wheels, resulting in this error.

### Quick Diagnosis

1. Check your Python version:
   ```bash
   python --version
   ```

2. If you see Python 3.11, 3.12, or 3.13, that's the problem!

### Solutions

#### Solution 1: Use Python 3.10 (Recommended)

**If you have Python 3.10 installed:**

1. Create a virtual environment with Python 3.10:
   ```bash
   py -3.10 -m venv venv
   ```

2. Activate the virtual environment:
   ```bash
   venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

**If you don't have Python 3.10:**

1. Download Python 3.10 from [python.org](https://www.python.org/downloads/)
2. Install it (check "Add Python to PATH" during installation)
3. Use it specifically:
   ```bash
   py -3.10 -m pip install -r requirements.txt
   ```

#### Solution 2: Install Python 3.10 and Set as Default

1. Download and install Python 3.10
2. During installation, check "Add Python to PATH"
3. Verify installation:
   ```bash
   python --version
   # Should show: Python 3.10.x
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

#### Solution 3: Use pyenv (Advanced)

If you need to manage multiple Python versions:

1. Install pyenv-win: https://github.com/pyenv-win/pyenv-win
2. Install Python 3.10:
   ```bash
   pyenv install 3.10.11
   pyenv local 3.10.11
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Verify Installation

After installing, verify MetaTrader5 works:

```python
import MetaTrader5 as mt5
print(mt5.__version__)
```

If this works without errors, installation was successful!

### Additional Notes

- **Windows Only**: MetaTrader5 only works on Windows
- **Python 3.6-3.10**: These are the only supported versions
- **Virtual Environment**: Highly recommended to use a virtual environment to avoid conflicts

### Still Having Issues?

1. Check you're on Windows (not WSL, not macOS, not Linux)
2. Verify Python version is 3.6-3.10
3. Try upgrading pip: `python -m pip install --upgrade pip`
4. Clear pip cache: `pip cache purge`
5. Try installing directly: `pip install MetaTrader5`

