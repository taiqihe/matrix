import sys
import os
import glob
import shutil
import time
import tempfile
from random import randint

from flask import Flask, request, session, make_response

from gmcs.deffile import MatrixDefFile
from gmcs.customize import customize_matrix
from gmcs.validate import validate_choices
from gmcs.choices import ChoicesFile
from gmcs.linglib.toolboximport import import_toolbox_lexicon


# Sometimes UTF-8 files have a (gratuitous) BOM. The utf-8-sig
# encoding will strip the BOM, but we want to always write files
# without it, so use regular utf-8 on write.
READ_ENCODING = "utf-8-sig"
WRITE_ENCODING = "utf-8"


app = Flask(__name__, static_folder="web")


def create_cookie():
    need_verify = False
    cookie = str(randint(1000, 9999))
    while os.path.exists("web/sessions/" + cookie):
        cookie = str(randint(1000, 9999))
    return cookie

@app.route("/", methods=["POST", "GET"])
@app.route("/matrix", methods=["POST", "GET"])
def matrix_main():
    cookie = request.cookies.get("session_id")
    if cookie is None:
        cookie = create_cookie()

    session_path = "web/sessions/" + cookie
    choices_path = os.path.join(session_path, "choices")

    if cookie and not os.path.exists(session_path):
        os.makedirs(session_path)
        # create a blank choices file
        with open(choices_path, "w", encoding=WRITE_ENCODING):
            pass

    matrixdef = MatrixDefFile("web/matrixdef")
    form_data = request.form.to_dict() | request.args.to_dict()

    # if the 'choices' field is defined, we have either the contents of an
    # uploaded choices file or the name of a sample choices file (which
    # will begin with 'sample-choices/') to replace the current choices.
    # TJT 2014-09-18: Get choices files from Language CoLLAGE links
    if "choices" in form_data or 'choices' in request.files:
        choices = form_data.get("choices", "")
        if choices:
            data = ""
            if choices.startswith("web/sample-choices/"):
                with open(choices, "r", encoding=READ_ENCODING) as f:
                    data = f.read()
            elif choices.startswith("collage/"):
                # Get choices files from CoLLAGE
                # should be 3 or 7 letter keys... doesn't work for other length keys
                if len(choices) in ((len("collage/") + 3), (len("collage/") + 7)):
                    import urllib.request
                    import urllib.error
                    import urllib.parse
                    import tarfile
                    import io

                    choices = (
                        "http://www.delph-in.net/matrix/language-"
                        + choices
                        + "/choices-final.tgz"
                    )
                    try:
                        tar = urllib.request.urlopen(choices)
                        tar = tarfile.open(fileobj=io.StringIO(tar.read()), mode="r|*")
                        for tarinfo in tar:
                            if (
                                tarinfo.isreg()
                                and tarinfo.name[-len("choices") :] == "choices"
                            ):
                                choicesData = tar.extractfile(tarinfo)
                                data = choicesData.read()
                                choicesData.close()
                                break  # Found the choices file...
                    except (
                        urllib.error.HTTPError,
                        urllib.error.URLError,
                        tarfile.TarError,
                    ):
                        data = ""
                    finally:
                        tar.close()
            if data or choices.endswith("/empty"):
                with open(choices_path, "w", encoding=WRITE_ENCODING) as f:
                    f.write(data)
        else:  # Uploaded choices data
            file = request.files.get('choices')
            if file is not None:
                file.save(choices_path)
                data = None

    # if the 'section' field is defined, we have submitted values to save
    if "section" in form_data:
        matrixdef.save_choices(form_data, choices_path)

    # if we have recieved toolbox files, then we want to add these lexical items after saving the toolbox configuration (done above).
    if "import_toolbox" in form_data:
        toolbox_files = []
        for key in list(form_data.keys()):
            if key[-10:] == "tbfilename" and form_data[key] != "":
                fout = tempfile.NamedTemporaryFile(dir=session_path)
                fout.write(form_data[key])
                toolbox_files.append(fout)
                form_data[key] = fout.name
        matrixdef.save_choices(form_data, choices_path)
        import_toolbox_lexicon(choices_path)
        for tbfile in toolbox_files:
            tbfile.close()

    # If the 'verbpred' field is defined, then the user wishes to generate more sentences with that predication
    if "verbpred" in form_data:
        response = matrixdef.more_sentences_page(
            session_path,
            form_data["grammar"],
            form_data["verbpred"],
            form_data["template"],
            cookie,
        )

    # Get a list of error messages, determined by validating the current
    # choices.  If the current choices are valid, the list will be empty.
    # --
    # no longer true, there can now be validation info messages.
    # nothing seems to depend on the list being empty #14 feb 2012
    try:
        vr = validate_choices(choices_path)
    except:
        exc = sys.exc_info()
        response = matrixdef.choices_error_page(choices_path, exc)

    # modified to support captcha
    if "customize" in form_data:
        # if the 'customize' field is defined, create a customized copy of the matrix
        # based on the current choices file
        # ERB 2006-10-03 Checking has_key here to enable local debugging.
        if "delivery" in form_data:
            arch_type = form_data["delivery"]
        else:
            arch_type = ""
        if arch_type not in ("tgz", "zip"):
            vr.err("delivery", "You must specify an archive type.")

        if vr.has_errors():
            response = matrixdef.error_page(vr)
        else:
            # If the user said it's OK, archive the choices file
            choices = ChoicesFile(choices_path)
            if choices.get("archive") == "yes":
                # create the saved-choices directory
                if not os.path.exists("saved-choices"):
                    os.mkdir("saved-choices")

                # look at the files in saved-choices, which will have names like
                # choices.N, figure out the next serial number, and copy the current
                # choices file to saved-choices/choices.N+1
                serial = 1
                for f in glob.glob("saved-choices/choices.*"):
                    i = f.rfind(".")
                    if i != -1:
                        num = f[i + 1 :]
                        if num.isdigit():
                            serial = max(serial, int(num) + 1)
                shutil.copy(choices_path, "saved-choices/choices." + str(serial))

            # Create the customized grammar
            try:
                grammar_dir = customize_matrix(session_path, arch_type)
            except:
                exc = sys.exc_info()
                response = matrixdef.customize_error_page(choices_path, exc)
                sys.exit()

            if "sentences" in form_data:
                response = matrixdef.sentences_page(session_path, grammar_dir, cookie)
            else:
                response = matrixdef.custom_page(session_path, grammar_dir, arch_type)
    elif "subpage" in form_data:
        response = matrixdef.sub_page(form_data["subpage"], cookie, vr)
    else:
        response = matrixdef.main_page(cookie, vr)
    response = make_response(response)
    response.set_cookie("session_id", cookie)
    return response


if __name__ == "__main__":
    app.run(debug=True)
