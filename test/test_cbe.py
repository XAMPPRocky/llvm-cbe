#!/usr/bin/env python3
import os
import io
import re
from glob import glob
from subprocess import call, Popen, PIPE
import pytest

# This configuration assumes that you are using the system LLVM.
# And that you created a subdir "build" to compile this from.
# In other cases, please modify the lines below.
TEST_DIR = os.path.dirname(os.path.abspath(__file__))

LLVM_TOOL_DIR = os.environ.get(
    'LLVMToolDir',
    os.path.join(TEST_DIR, '..', 'build', 'tools', 'llvm-cbe'))

LLVM_CBE_PATH = os.path.join(LLVM_TOOL_DIR, 'llvm-cbe')
LLI_PATH = 'lli'

CBE_FLAGS = [
    # Harder to get right than early declarations, so more value to test it.
    '-cbe-declare-locals-late',
]

COMMON_CFLAGS = [
    '-Iinclude/',
    '-g',
    '-Wall',
    '-Wno-unused-function',
    '-Wno-unused-variable',
    '-Werror',
]

GCC = 'gcc'
GCCFLAGS = COMMON_CFLAGS + [
    '-Wno-error=unused-but-set-variable',
    '-Wno-unused-but-set-variable',
    '-Wno-builtin-declaration-mismatch',
    '-Wno-error=builtin-declaration-mismatch',
    '-latomic',
]

CLANG = 'clang'
CLANGPP = 'clang++'
CLANGFLAGS = COMMON_CFLAGS + [
    '-Wno-error=unused-variable',
    '-Wno-unused-variable',
]

# exit code used by tests to indicate success
TEST_SUCCESS_EXIT_CODE = 6
# exit code used by tests to indicate xfail
TEST_XFAIL_EXIT_CODE = 25


def check_no_output(args):
    proc = Popen(args, stdout=PIPE, stderr=PIPE)
    out, err = proc.communicate()

    if out or err:
        out = out.decode("utf-8")
        err = err.decode("utf-8")

        msg_stream = io.StringIO()
        print(f"Got unexpected output from process", file=msg_stream)

        print(f"args: {args}", file=msg_stream)
        print(file=msg_stream)

        print(f"stdout:", file=msg_stream)
        print(out, file=msg_stream)
        print(file=msg_stream)

        print(f"stderr:", file=msg_stream)
        print(err, file=msg_stream)
        print(file=msg_stream)

        raise Exception(msg_stream.getvalue())

    assert not proc.returncode, f"process exit code {proc.returncode}"


def _compile_c(compiler, flags, c_filename, output_filename):
    check_no_output([compiler, c_filename, '-o', output_filename] + flags)
    return output_filename


def compile_gcc(c_filename, output_filename, flags=None):
    flags = flags or []
    return _compile_c(GCC, GCCFLAGS + flags, c_filename, output_filename)


def compile_clang(c_filename, output_filename, flags=None, cplusplus=False):
    flags = flags or []
    return _compile_c(
        CLANGPP if cplusplus else CLANG,
        CLANGFLAGS + flags,
        c_filename,
        output_filename)


def compile_to_ir(c_filename, ir_filename, flags=None, cplusplus=False):
    flags = list(flags or [])
    flags += ['-S', '-emit-llvm']
    return compile_clang(
        c_filename, ir_filename, flags=flags, cplusplus=cplusplus)


def run_llvm_cbe(ir_filename, c_filename):
    check_no_output([LLVM_CBE_PATH, ir_filename, *CBE_FLAGS, '-o', c_filename])
    return c_filename


def collect_tests(base_dir, extensions):
    for dirname, _, filenames in os.walk(base_dir):
        for fn in filenames:
            if fn.startswith('test_') and fn.endswith(extensions):
                yield os.path.join(dirname, fn)


def get_test_name_from_filename(test_path):
    return os.path.splitext(os.path.basename(test_path))[0]


def check_xfail(test_path):
    code = open(test_path).read()
    m = re.search(r'(?m)^// xfail: (.+)', code)
    if m:
        pytest.xfail(m.group(1))


@pytest.mark.parametrize(
    'cflags',
    [['-O0'], ['-O1'], ['-O2'], ['-O3']],
    ids=lambda flags: ' '.join(flags)
)
@pytest.mark.parametrize(
    'test_filename',
    collect_tests(TEST_DIR, ('.c', '.cpp')),
    ids=get_test_name_from_filename,
)
def test_consistent_return_value_c(test_filename, tmpdir, cflags):
    """
    Compile and execute a C or C++ file with clang, and compare its exit code
    with the exit code when compiled with llvm-cbe followed by gcc.

    Also, the exit code must be TEST_SUCCESS_EXIT_CODE for success or
    TEST_XFAIL_EXIT_CODE or expected failures.
    """

    check_xfail(test_filename)

    cplusplus = test_filename.endswith('.cpp')

    # make sure CBE doesn't have any errors before trying to compile
    # executables
    ir = compile_to_ir(
        test_filename, tmpdir / 'ir.ll', flags=cflags, cplusplus=cplusplus)
    cbe_c = run_llvm_cbe(ir, tmpdir / 'cbe.c')

    regular_exe = compile_clang(
        test_filename,
        tmpdir / 'regular.exe',
        flags=cflags,
        cplusplus=cplusplus)
    regular_retval = call([regular_exe])
    print('regular executable returned', regular_retval)
    assert regular_retval in [TEST_SUCCESS_EXIT_CODE, TEST_XFAIL_EXIT_CODE]

    cbe_exe = compile_gcc(cbe_c, tmpdir / 'cbe.exe', flags=cflags)
    cbe_retval = call([cbe_exe])
    print('cbe output returned', cbe_retval)
    assert cbe_retval == regular_retval


@pytest.mark.parametrize(
    'test_filename',
    collect_tests(TEST_DIR, ('.ll', )),
    ids=get_test_name_from_filename,
)
def test_consistent_return_value_ll(test_filename, tmpdir):
    """
    Execute an LLVM IR file with lli, and compare its exit code with the exit
    code when compiled with llvm-cbe followed by gcc.

    Also, the exit code must be TEST_SUCCESS_EXIT_CODE for success or
    TEST_XFAIL_EXIT_CODE or expected failures.
    """

    check_xfail(test_filename)

    lli_retval = call([LLI_PATH, test_filename])
    print('lli exit code was', lli_retval)
    assert lli_retval in [TEST_SUCCESS_EXIT_CODE, TEST_XFAIL_EXIT_CODE]

    cbe_c = run_llvm_cbe(test_filename, tmpdir / 'cbe.c')
    cbe_exe = compile_gcc(cbe_c, tmpdir / 'cbe.exe')
    cbe_retval = call([cbe_exe])
    print('cbe output returned', cbe_retval)
    assert cbe_retval == lli_retval


if __name__ == '__main__':
    raise SystemExit("run me using pytest")
