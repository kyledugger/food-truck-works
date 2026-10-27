# Password hash generator

New files only: `tools/hash_password.py` and this guide. Extract the ZIP into
your existing Food Truck Works project root, keeping the tools directory.
No existing application files or requirements change.

From your project directory, use the same Python environment as the app:

```powershell
.\.venv\Scripts\python.exe tools\hash_password.py
```

With the virtual environment already activated (Windows, Linux, or macOS):

```sh
python tools/hash_password.py
```

Enter the new password twice when prompted. Input is hidden. The script uses
the project's `auth.validate_password`, `hash_password`, and `verify_password`.
The current policy accepts 10–128 characters. Spaces are preserved.

Copy the entire output line, beginning with `$argon2id$`, into the intended
user's `users.password_hash` field using your database editor. Keep all `$`
characters and the full hash intact. Each run produces a new salted hash,
even for the same password; this is expected.

The tool does not take passwords as command-line arguments, write password
files, load database configuration, or connect to the database. Errors go to
stderr, and failed validation or confirmation produces no hash on stdout.
Run in a terminal that supports hidden input; cancellation is supported.

Changing a stored password hash directly does not itself invalidate existing
signed-cookie sessions. This tool only generates the replacement hash.
