from setuptools import setup
from Cython.Build import cythonize

setup(
    ext_modules=cythonize(["app.py", "ai_agent.py"])
)

