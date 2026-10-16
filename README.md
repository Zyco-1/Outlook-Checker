# Outlook-Checker

A Python-based tool for inspecting Microsoft account authentication flows, interpreting login responses, handling proxy connections, and recording diagnostic results.

## Features

- **Authentication flow analysis** — inspects login bootstrap responses, credential-type information, password-validation responses, and final submission results.
- **Status classification** — identifies outcomes such as account-not-found responses, authentication challenges, password-reset requirements, rate limits, and incomplete flows.
- **Concurrent execution** — uses Python's `ThreadPoolExecutor` to process multiple input entries concurrently.
- **Proxy support** — supports HTTP, HTTPS, SOCKS4, SOCKS4a, SOCKS5, and SOCKS5h proxy configurations where supported by the installed networking dependencies.
- **Proxy validation** — optionally tests proxy connectivity before starting.
- **Configurable output** — supports multiple result-saving modes and custom output paths.
- **Diagnostic logging** — optional verbose mode saves HTML responses for troubleshooting.
- **Graceful shutdown** — handles `Ctrl+C` to stop processing and cancel pending tasks.
- **Interactive and CLI modes** — configure the program through prompts or command-line arguments.

## Requirements

- Python 3.10 or newer recommended
- `requests`
- Network access to the relevant Microsoft authentication endpoints
- Proxy dependencies for the proxy protocols you intend to use

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Create and activate a virtual environment:

**Windows**

```powershell
python -m venv .venv
.venv\Scripts\activate
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the dependency:

```bash
python -m pip install requests
```

## Usage

### Interactive mode

Run the script without arguments:

```bash
python main.py
```

The program prompts for configuration, including input and output file paths, worker count, processing delay, save mode, proxy settings, and verbose logging.

### Command-line mode

Display the available options:

```bash
python main.py --help
```

The command-line interface supports the following arguments:

| Argument | Description |
|---|---|
| `-f`, `--file` | Input file path (required in CLI mode) |
| `-o`, `--output` | Output file path; defaults to `results.txt` |
| `-w`, `--workers` | Maximum concurrent workers; defaults to `10` |
| `-d`, `--delay` | Delay before processing each entry |
| `-m`, `--mode` | Result-saving mode |
| `-v`, `--verbose` | Enable diagnostic HTML output |
| `-p`, `--proxies` | Proxy-list file |
| `-pm`, `--proxy-mode` | Proxy selection strategy |
| `--no-precheck` | Skip proxy validation |
| `--proxy-timeout` | Proxy validation timeout in seconds |

## Configuration

### Result-saving modes

| Mode | Description |
|---|---|
| `all` | Save all processed results |
| `working` | Save results classified as valid by the program |
| `working_undecided` | Include selected ambiguous or incomplete outcomes |
| `working_undecided_recover` | Also include recovery-related outcomes |

These labels describe the program's classification logic. They are not guarantees that Microsoft will accept a login or that a session is fully authenticated.

### Proxy formats

The parser accepts several common proxy representations, including:

```text
127.0.0.1:8080
http://127.0.0.1:8080
username:password@127.0.0.1:8080
127.0.0.1:8080:username:password
username:password:127.0.0.1:8080
```

The parser also recognizes comma- and semicolon-delimited variants and bracketed IPv6 addresses. Ambiguous formats may not be interpreted as intended.

Proxy support depends on the protocol adapter installed in your Python environment. For example, SOCKS proxies generally require the SOCKS extra for `requests`:

```bash
python -m pip install "requests[socks]"
```

### Diagnostic output

When verbose mode is enabled, the program can save HTML responses to help investigate authentication-flow changes.

Treat these files as sensitive. Authentication responses can contain temporary tokens, identifiers, cookies, or other session-related information. Do not commit them to a public repository.

## How It Works

At a high level, the code follows this sequence:

1. Initializes a session and retrieves the initial login page.
2. Extracts relevant form fields and flow tokens.
3. Retrieves credential-type information.
4. Interprets password-validation responses.
5. Examines the final response and redirects when the flow reaches that stage.
6. Classifies the observed result and records diagnostic information.
7. Aggregates processing statistics when the run completes.

Microsoft may change its authentication flow, response fields, and security requirements. Consequently, classifications can be incomplete or inaccurate when the service changes.

## Project Structure

```text
.
├── main.py       # Authentication-flow analysis and CLI
├── README.md     # Project documentation
├── accounts.txt  # Local test input; do not commit
├── proxies.txt   # Optional local proxy list; do not commit
└── results.txt   # Generated diagnostic output; do not commit
```

The text files shown above are examples of local runtime files, not files included in the uploaded source.

## Security and Responsible Use

- Use this software only in environments you own or are explicitly authorized to test.
- Do not use credentials belonging to other people or process leaked credential lists.
- Never publish passwords, authentication tokens, cookies, proxy credentials, or raw authentication responses.
- Avoid committing account inputs, diagnostic HTML, and generated results.
- Respect Microsoft's applicable terms, rate limits, and security controls.
- Prefer Microsoft's official authentication libraries and supported sign-in interfaces when building production applications.

## Limitations

- Authentication endpoints and response formats can change without notice.
- A successful intermediate response does not necessarily mean a complete authenticated session.
- Proxy connectivity checks do not guarantee that a proxy will remain available.
- Concurrent requests may encounter throttling, transient network errors, or incomplete responses.
- The program does not provide a guarantee of classification accuracy.

## License

No license has been specified. Add a `LICENSE` file before distributing the project so users know what permissions and restrictions apply.
---

**Disclaimer:** This project is provided for authorized debugging and security testing. Users are responsible for ensuring that their use complies with applicable laws, service terms, and account-owner permissions.
# **Educational Purposes Only**
