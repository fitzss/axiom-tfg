from setuptools import setup

package_name = "axiom_preflight_nav2"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Axiom Contributors",
    maintainer_email="axiom@example.com",
    description="Axiom pre-flight gate proxy for Nav2 NavigateToPose actions.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "axiom-preflight-nav2 = axiom_preflight_nav2.preflight_proxy_node:main",
        ],
    },
)
