class AiJury < Formula
  include Language::Python::Virtualenv

  desc "Cross-vendor multi-agent PR & code review jury"
  homepage "https://ai-jury.dev/"
  url "https://files.pythonhosted.org/packages/0e/27/4b451ec60c5f20629dc52be0112da29fb981289c3d9d3e1cbe4a244ef135/ai_jury-1.14.2.tar.gz"
  sha256 "17b8b747f96dde5c832cea2c32eca266b6068118da3bb358dd54fd1fff0f8b6e"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "jury 1.14.2", shell_output("#{bin}/jury --version")
    assert_match "error: provide one of", shell_output("#{bin}/jury --mock 2>&1", 1)
  end
end
