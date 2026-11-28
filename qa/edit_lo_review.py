"""Edit the disposable QA draft via an isolated LibreOffice's bundled Python/UNO."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.connection import NoConnectException


def main():
    document = Path(sys.argv[1]).resolve()
    pipe = "ttf_" + uuid.uuid4().hex
    app = Path(sys.executable).with_name("soffice.exe")
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    with tempfile.TemporaryDirectory(prefix="ttf-lo-") as work:
        proc = subprocess.Popen([str(app), "-env:UserInstallation=" + Path(work).as_uri(),
                                 "--headless", "--nologo", "--nodefault", "--norestore",
                                 "--accept=pipe,name=" + pipe + ";urp;"])
        try:
            for _ in range(120):
                try:
                    ctx = resolver.resolve("uno:pipe,name=" + pipe + ";urp;StarOffice.ComponentContext")
                    break
                except NoConnectException:
                    time.sleep(0.25)
            else:
                raise RuntimeError("LibreOffice private pipe did not become ready")
            desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            hidden = PropertyValue()
            hidden.Name, hidden.Value = "Hidden", True
            doc = desktop.loadComponentFromURL(document.as_uri(), "_blank", 0, (hidden,))
            if not doc:
                raise RuntimeError("LibreOffice could not open the draft")
            try:
                cursor = doc.Text.createTextCursor()
                cursor.gotoEnd(False)
                doc.Text.insertString(cursor, " QA-EDIT-8642", False)
                note = doc.createInstance("com.sun.star.text.textfield.Annotation")
                note.Author, note.Content = "QA Editor", "QA reviewer annotation"
                doc.Text.insertTextContent(cursor, note, False)
                doc.store()
            finally:
                doc.close(True)
            desktop.terminate()
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)


if __name__ == "__main__":
    main()
