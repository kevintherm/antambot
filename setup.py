from setuptools import setup
from Cython.Build import cythonize
import os

files_to_compile = ["bot.py", "captcha.py"]

setup(
    ext_modules=cythonize(files_to_compile, compiler_directives={'language_level': "3"})
)
