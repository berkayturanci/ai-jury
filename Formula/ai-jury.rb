class AiJury < Formula
  include Language::Python::Virtualenv

  desc "Cross-vendor multi-agent PR & code review jury"
  homepage "https://berkayturanci.github.io/ai-jury/"
  url "https://files.pythonhosted.org/packages/c4/25/23ce4ebc54385cd8ce233da9ed07c48804421c5c25f3ab223908b1ce2163/ai_jury-1.13.0.tar.gz"
  sha256 "dbc048b6955adee34bfdea435e7a31db133329e48b57a61b0688fc2f06270809"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "jury 1.13.0", shell_output("#{bin}/jury --version")
    assert_match "error: provide one of", shell_output("#{bin}/jury --mock 2>&1", 1)
  end
end
